"""SSH connection base connector using paramiko."""
import asyncio
import logging
import os
import subprocess
import time
from pathlib import Path

from sprinkler.connect.base import BaseConnector, _strip_ansi
from sprinkler.connect.targets import ConnectionTarget

logging.getLogger("paramiko.transport").setLevel(logging.CRITICAL)


class _ChannelReader:
    def __init__(self, channel):
        self._channel = channel

    async def read(self, size=4096):
        while True:
            if self._channel.recv_ready():
                data = self._channel.recv(size)
                return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
            await asyncio.sleep(0.1)


class _ChannelWriter:
    def __init__(self, channel):
        self._channel = channel

    async def drain(self):
        pass

    def write(self, data):
        self._channel.send(data)

    def close(self):
        self._channel.close()

    async def wait_closed(self):
        pass


class SSHConnector(BaseConnector):
    def _run_commands(self, target: ConnectionTarget, commands: list[str], on_result=None):
        raise NotImplementedError("Subclasses must implement _run_commands")

    def _establish_connection(self, target: ConnectionTarget):
        target = ConnectionTarget.from_value(target)
        target_error = target.validate()
        if target_error:
            raise ValueError(target_error)

        try:
            import paramiko
        except Exception as exc:
            raise RuntimeError(f"dependency: paramiko unavailable: {exc}") from exc

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        label = self.target_label(target)
        self.log_info(f'正在尝试连接到 {label}')

        try:
            self._connect_paramiko(client, target, paramiko)
        except Exception as exc:
            client.close()
            error_type = self._classify_paramiko_connect_error(exc, paramiko)
            raise RuntimeError(f"{error_type}: {exc}") from exc

        self.log_info(f'成功连接到 {label}')
        return client

    def _connect_paramiko(self, client, target: ConnectionTarget, paramiko):
        config = self._ssh_config(target.host, paramiko)
        identity_files = self._identity_files(config.get("identityfile"))
        has_password = bool(target.password)
        kwargs = {
            "hostname": config.get("hostname") or target.host,
            "port": int(config.get("port") or target.port),
            "username": target.user,
            "password": target.password or None,
            "timeout": self.connect_timeout,
            "banner_timeout": self.connect_timeout,
            "auth_timeout": self.connect_timeout,
            "allow_agent": not has_password,
            "look_for_keys": not has_password,
        }
        if identity_files:
            kwargs["key_filename"] = identity_files
        proxycommand = str(config.get("proxycommand") or "").strip()
        if proxycommand and proxycommand.lower() != "none":
            kwargs["sock"] = paramiko.ProxyCommand(proxycommand)
        client.connect(**kwargs)

    def _ssh_config(self, host, paramiko):
        cache = getattr(self, "_ssh_config_cache", {})
        if host in cache:
            return cache[host]

        config = self._openssh_effective_config(host) or self._paramiko_ssh_config(host, paramiko)
        cache[host] = config
        self._ssh_config_cache = cache
        return config

    def _openssh_effective_config(self, host):
        try:
            output = subprocess.run(
                ["ssh", "-G", str(host or "")],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
                check=False,
            ).stdout
        except Exception:
            return {}
        config = {}
        for line in output.splitlines():
            key, _, value = line.partition(" ")
            key = key.strip().lower()
            value = value.strip()
            if key == "identityfile":
                config.setdefault(key, []).append(value)
            elif key in {"hostname", "port", "user", "proxycommand"}:
                config[key] = value
        return config

    def _paramiko_ssh_config(self, host, paramiko):
        path = Path.home() / ".ssh" / "config"
        if not path.exists():
            return {}
        try:
            config = paramiko.SSHConfig()
            with path.open("r", encoding="utf-8", errors="replace") as fp:
                config.parse(fp)
            return config.lookup(str(host or ""))
        except Exception:
            return {}

    def _identity_files(self, values):
        if not values:
            return []
        items = values if isinstance(values, (list, tuple)) else [values]
        paths = []
        for item in items:
            path = os.path.expanduser(str(item or "").strip().strip("'\""))
            if path and path.lower() != "none" and Path(path).exists():
                paths.append(path)
        return paths

    def _run_ssh_command(self, client, target: ConnectionTarget, cmd: str):
        return self._run_paramiko_command(client, target, cmd)

    def _run_paramiko_command(self, client, target: ConnectionTarget, cmd: str):
        import socket as _socket
        import time as _time

        start = _time.time()
        try:
            _stdin, stdout, stderr = client.exec_command(cmd, timeout=self.command_timeout)
            stdout.channel.settimeout(self.command_timeout)
            out = self._decode_text(stdout.read())
            err = self._decode_text(stderr.read())
            rc = int(stdout.channel.recv_exit_status())
            return self.result_processor.make(
                target, cmd, rc == 0, rc, "" if rc == 0 else "command", out, err, _time.time() - start, self.backend_name()
            )
        except (_socket.timeout, TimeoutError) as exc:
            return self.result_processor.make(
                target, cmd, False, 255, "timeout", "", str(exc), _time.time() - start, self.backend_name()
            )
        except Exception as exc:
            return self.result_processor.make(
                target, cmd, False, 255, "command", "", str(exc), _time.time() - start, self.backend_name()
            )

    def _classify_paramiko_connect_error(self, exc: Exception, paramiko):
        import socket as _socket

        if isinstance(exc, (_socket.timeout, TimeoutError)):
            return "timeout"
        if isinstance(exc, (paramiko.AuthenticationException, paramiko.BadAuthenticationType)):
            return "auth"
        if isinstance(exc, paramiko.SSHException):
            text = str(exc).casefold()
            if self._looks_like_backend_error(text):
                return "backend"
            if "timed out" in text or "timeout" in text:
                return "timeout"
            if "authentication" in text or "auth" in text or "password" in text:
                return "auth"
            return "network"
        if isinstance(exc, (paramiko.ssh_exception.NoValidConnectionsError, ConnectionRefusedError, OSError, _socket.gaierror)):
            text = str(exc).casefold()
            if "timed out" in text or "timeout" in text:
                return "timeout"
            return "network"
        return "unknown"

    def connect_client(self, target: ConnectionTarget):
        return self._establish_connection(target)

    def _collect_passwords(self, target):
        passwords_to_try = []
        pwd = getattr(target, 'password', None) if hasattr(target, 'password') else None
        if pwd:
            passwords_to_try.append(pwd)
        try:
            from sprinkler.utils.password_store import get_default_passwords
            for pwd in get_default_passwords("bmc"):
                if pwd not in passwords_to_try:
                    passwords_to_try.append(pwd)
        except ImportError:
            pass
        return passwords_to_try

    def _connect_with_passwords(self, target: ConnectionTarget, error_classifier=None):
        import paramiko

        if error_classifier is None:
            error_classifier = lambda exc: self._classify_paramiko_connect_error(exc, paramiko)

        passwords_to_try = self._collect_passwords(target)
        client = None
        last_error = None

        for pwd in passwords_to_try or [""]:
            try:
                self.log_info(f'正在尝试连接到 {self.target_label(target)}')
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(
                    hostname=target.host,
                    port=target.port,
                    username=target.user,
                    password=pwd,
                    timeout=self.connect_timeout,
                    banner_timeout=self.connect_timeout,
                    auth_timeout=self.connect_timeout,
                    allow_agent=False,
                    look_for_keys=False,
                )
                return client, None
            except Exception as exc:
                last_error = exc
                if client:
                    client.close()
                client = None
                continue

        return None, last_error
