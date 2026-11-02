"""Base connector for SSH/Telnet connections."""
import asyncio
import re
import socket
import time
from abc import ABC, abstractmethod


def _strip_ansi(text: str) -> str:
    return re.sub(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\r', '', text)


from sprinkler.connect.targets import ConnectionTarget


class NoopRunLog:
    def info(self, message):
        pass

    def fail(self, message):
        pass

    def warn(self, message):
        pass

    def log(self, level, message):
        """Generic logger sink (level-aware). Noop by default."""
        pass

    def ok(self, message):
        pass


class ResultProcessor:
    def make(self, target: ConnectionTarget, cmd: str, ok: bool, rc: int, error_type: str, stdout: str, stderr: str, elapsed: float, backend: str):
        return {
            "target": BaseConnector.target_label(target),
            "host": target.host,
            "port": target.port,
            "user": target.user,
            "backend": backend or "none",
            "cabinet_name": (target.meta or {}).get("cabinet_name", ""),
            "kind": (target.meta or {}).get("kind", ""),
            "node": (target.meta or {}).get("node", ""),
            "cmd": cmd,
            "ok": bool(ok),
            "rc": int(rc),
            "error_type": error_type or "",
            "stdout": stdout or "",
            "stderr": stderr or "",
            "elapsed": round(float(elapsed), 3),
        }

    def failed_many(self, target: ConnectionTarget, commands: list[str], error_type: str, message: str, elapsed: float, backend: str):
        return [self.make(target, cmd, False, 255, error_type, "", message, elapsed, backend) for cmd in commands]

    def first_reason(self, result: dict):
        text = str(result.get("stderr") or result.get("stdout") or "").strip()
        for line in text.splitlines():
            line = line.strip()
            if line:
                return line
        return str(result.get("error_type") or "unknown")


class BaseConnector(ABC):
    BACKEND_ERROR_PATTERNS = (
        "no matching host key type",
        "no matching key exchange method",
        "unable to negotiate",
        "kex_exchange_identification",
        "algorithm",
        "incompatible",
        "protocol banner",
    )

    def __init__(self, args=None, result_processor=None, run_log=None):
        self.args = args
        self.result_processor = result_processor or ResultProcessor()
        self.run_log = run_log or NoopRunLog()
        self.connect_timeout = self._positive_int_arg("connect_timeout", 4)
        self.command_timeout = self._positive_int_arg("timeout", 30)

    def run_target(self, target: ConnectionTarget, commands: list[str], on_result=None):
        target = ConnectionTarget.from_value(target)
        target_error = target.validate()
        if target_error:
            return self.result_processor.failed_many(target, commands, "target", target_error, 0.0, self.backend_name())

        start = time.time()
        try:
            return self._run_commands(target, commands, on_result=on_result)
        except Exception as exc:
            return self.result_processor.failed_many(target, commands, self._classify_error(exc), str(exc), time.time() - start, self.backend_name())

    def run_command(self, client, target: ConnectionTarget, command: str):
        results = self.run_target(target, [command])
        return results[0] if results else {}

    @abstractmethod
    def _run_commands(self, target: ConnectionTarget, commands: list[str], on_result=None):
        pass

    @abstractmethod
    def backend_name(self):
        pass

    @staticmethod
    def target_label(target: ConnectionTarget) -> str:
        meta = target.meta or {}
        cabinet_name = meta.get("cabinet_name", "")
        kind = meta.get("kind", "")
        node = int(meta.get("node", 0)) + 1
        if cabinet_name and kind and str(node):
            device, _, method = kind.partition("_")
            kind_label = f"{device.upper()}{node}-{method.upper()}" if device and method else kind
            return f"{cabinet_name}-{kind_label}"
        return target.label()

    def _positive_int_arg(self, name: str, default: int):
        value = getattr(self.args, name, default)
        try:
            value = int(value) if value is not None else default
        except (TypeError, ValueError) as exc:
            raise ValueError(f"-{name.replace('_', '-')} must be positive") from exc
        if value <= 0:
            raise ValueError(f"-{name.replace('_', '-')} must be positive")
        return value

    @staticmethod
    def _decode_text(value):
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value or "")

    def _looks_like_backend_error(self, text: str):
        return any(pattern in text for pattern in self.BACKEND_ERROR_PATTERNS)

    async def _read_eager(self, reader):
        buffer = ""
        try:
            while True:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=0.1)
                if not chunk:
                    break
                buffer += chunk
        except asyncio.TimeoutError:
            pass
        return buffer

    async def _read_until(self, reader, patterns: list[str], timeout: float) -> str:
        start = time.time()
        buffer = ""
        lower_patterns = [p.lower() for p in patterns]
        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=0.5)
                if chunk:
                    buffer += chunk
                    lower_buf = buffer.lower()
                    for pat in lower_patterns:
                        if pat in lower_buf:
                            return buffer
            except asyncio.TimeoutError:
                pass
            if time.time() - start >= timeout:
                return buffer

    def _classify_error(self, exc: Exception):
        if isinstance(exc, (socket.timeout, TimeoutError, asyncio.TimeoutError)):
            return "timeout"
        if isinstance(exc, (ConnectionRefusedError, OSError, socket.gaierror)):
            return "network"
        return "unknown"

    def log_info(self, msg):
        self.run_log.info(msg)

    def log_fail(self, msg):
        self.run_log.fail(msg)

    def log_warn(self, msg):
        self.run_log.warn(msg)

    async def _run_telnet_command(self, reader, writer, target: ConnectionTarget, cmd: str):
        start_time = time.time()

        max_retries = 3
        current_try = 0
        final_out = ""
        is_success = False
        error_msg = ""
        error_type = "command"

        while current_try < max_retries:
            current_try += 1
            try:
                await self._read_eager(reader)

                final_cmd = cmd + " \r\n"
                for char in final_cmd:
                    writer.write(char)
                    time.sleep(0.001)
                await writer.drain()

                output_buffer = ""
                last_output_time = time.time()
                await asyncio.sleep(0.05)

                while True:
                    chunk = await self._read_eager(reader)
                    if chunk:
                        output_buffer += chunk
                        last_output_time = time.time()

                    if re.search(r'<[^\r\n>]+>\s*$', output_buffer) or re.search(r'\[[^\r\n\]]+\]\s*$', output_buffer):
                        break

                    if (time.time() - start_time) > self.command_timeout:
                        error_type = "timeout"
                        raise TimeoutError("Command execution timed out")

                    if (time.time() - last_output_time) > 5 and output_buffer:
                        break

                    await asyncio.sleep(0.02)

                ansi_escape = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
                output_buffer = ansi_escape.sub('', output_buffer)

                if "QOS USED CREDIT TABLE" in output_buffer:
                    output_buffer = output_buffer[output_buffer.rfind("QOS USED CREDIT TABLE"):]

                if "USED" in cmd:
                    output_buffer = re.sub(r'inc\s+U\s*S\s*E\s*D\s*\|', 'inc USED |', output_buffer)

                if "Error:" in output_buffer or "Unrecognized command" in output_buffer:
                    raise ValueError("Switch returned CLI error")

                lines = output_buffer.split('\n')
                cleaned_lines = []
                echo_removed = False
                for line in lines:
                    stripped = line.strip().replace('\r', '').replace('\xa0', ' ')
                    if not echo_removed and cmd in stripped:
                        echo_removed = True
                        continue
                    if stripped:
                        cleaned_lines.append(line.rstrip('\r'))

                out = '\n'.join(cleaned_lines)

                if "USED" in cmd and "QOS USED CREDIT TABLE" not in out:
                    raise ValueError("Target data missing in final output")

                final_out = out
                is_success = True
                break

            except (socket.timeout, TimeoutError, asyncio.TimeoutError, ValueError) as exc:
                if current_try < max_retries:
                    error_msg = ""
                    writer.write("\r\n\r\n\r\n")
                    await writer.drain()
                    await self._read_eager(reader)
                    await asyncio.sleep(0.3)
                else:
                    error_msg = str(exc)
                    error_type = "timeout" if isinstance(exc, (socket.timeout, TimeoutError, asyncio.TimeoutError)) else "command"
                    final_out = ""
                    is_success = False
                    break

            except Exception as exc:
                error_msg = str(exc)
                error_type = "command"
                final_out = ""
                is_success = False
                break

        exit_code = 0 if is_success else 255
        final_error_type = "" if is_success else error_type

        return self.result_processor.make(
            target, cmd, is_success, exit_code, final_error_type,
            final_out, error_msg, time.time() - start_time, self.backend_name()
        )

    async def _try_password_login(self, reader, writer, target: ConnectionTarget, expect_prompts: list[str]) -> bool:
        from sprinkler.utils.password_store import get_default_passwords

        passwords = [target.password] if target.password else []
        for pwd in get_default_passwords("1213"):
            if pwd not in passwords:
                passwords.append(pwd)

        error_keywords = ["times out", "invalid"]
        pass_prompts = ["Password:"]

        for pwd in passwords:
            writer.write(pwd + "\n")
            await writer.drain()
            output = await self._read_until(reader, expect_prompts + error_keywords + pass_prompts, timeout=self.command_timeout)
            if any(p in output for p in expect_prompts):
                return True
            if any(kw in output for kw in error_keywords) or any(p in output for p in pass_prompts):
                writer.write("\n")
                await writer.drain()
                await self._read_until(reader, pass_prompts, timeout=self.command_timeout)
                continue

        return False

    async def _try_huawei_login(self, reader, writer, target: ConnectionTarget, expect_prompts: list[str]) -> bool:
        from sprinkler.utils.password_store import get_default_passwords

        passwords = get_default_passwords("huawei")
        if not passwords and target.password:
            passwords = [target.password]

        error_keywords = ["Username or password error"]

        for pwd in passwords:
            writer.write("huawei\n")
            await writer.drain()

            output = await self._read_until(reader, ["Password:"], timeout=self.command_timeout)
            if "Password:" not in output:
                writer.write("\n")
                await writer.drain()
                await self._read_until(reader, ["Username:"], timeout=self.command_timeout)
                continue

            writer.write(pwd + "\n")
            await writer.drain()

            output = await self._read_until(
                reader,
                expect_prompts + error_keywords + ["Username:"],
                timeout=self.command_timeout,
            )

            if any(p in output for p in expect_prompts):
                return True

            if "Username:" not in output:
                writer.write("\n")
                await writer.drain()
                await self._read_until(reader, ["Username:"], timeout=self.command_timeout)

        return False
