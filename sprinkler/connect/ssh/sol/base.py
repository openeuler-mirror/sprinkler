"""Base connector for SSH BMC with SOL (Serial Over LAN) - 1213 style."""
import asyncio
import time

from sprinkler.connect.base import _strip_ansi
from sprinkler.connect.ssh.base import SSHConnector, _ChannelReader, _ChannelWriter
from sprinkler.connect.targets import ConnectionTarget


class SOL1213Connector(SSHConnector):
    INIT_COMMANDS = []
    ERROR_CLASSIFIER = "paramiko"

    def _run_commands(self, target: ConnectionTarget, commands: list[str], on_result=None):
        start = time.time()
        try:
            import paramiko
        except Exception as exc:
            return self.result_processor.failed_many(
                target, commands, "dependency", f"paramiko unavailable: {exc}", 0.0, self.backend_name()
            )

        label = self.target_label(target)

        def error_classifier(exc):
            return self._classify_connect_error(exc, paramiko)

        client, last_error = self._connect_with_passwords(target, error_classifier)

        if not client:
            error_type = self._classify_connect_error(last_error, paramiko)
            return self.result_processor.failed_many(
                target, commands, error_type, f"All passwords failed: {last_error}", time.time() - start, self.backend_name()
            )

        try:
            channel = client.invoke_shell()
            channel.settimeout(self.command_timeout)

            if not self._wait_channel_prompt(channel):
                return self.result_processor.failed_many(
                    target, commands, "init", "Failed to get BMC shell prompt", time.time() - start, self.backend_name()
                )

            for i, cmd in enumerate(self.INIT_COMMANDS):
                is_sol_switch = (i == len(self.INIT_COMMANDS) - 1)
                ok = self._send_init_command(channel, cmd, wait_prompt=not is_sol_switch)
                if not ok:
                    return self.result_processor.failed_many(
                        target, commands, "init", f"Init command failed: {cmd}", time.time() - start, self.backend_name()
                    )

            reader = _ChannelReader(channel)
            writer = _ChannelWriter(channel)

            if not asyncio.run(self._login_sol(reader, writer, target)):
                return self.result_processor.failed_many(
                    target, commands, "init", "SOL login failed", time.time() - start, self.backend_name()
                )

            self.log_info(f'成功连接到 {label}, 开始执行指令')
            results = asyncio.run(self._run_sol_commands(reader, writer, target, commands, on_result))
            self.log_info(f'{label} 指令执行完成')
            return results

        except Exception as exc:
            return self.result_processor.failed_many(
                target, commands, self._classify_error(exc), str(exc), time.time() - start, self.backend_name()
            )
        finally:
            if client:
                client.close()

    def _classify_connect_error(self, exc, paramiko):
        if isinstance(exc, paramiko.AuthenticationException):
            return "auth"
        if self.ERROR_CLASSIFIER == "paramiko":
            return self._classify_paramiko_connect_error(exc, paramiko)
        return self._classify_error(exc)

    def _wait_channel_prompt(self, channel, timeout=None) -> bool:
        import socket
        prompts = ["$ ", "# ", "> ", "]"]
        if timeout is None:
            timeout = self.command_timeout
        buffer = ""
        deadline = time.time() + timeout
        while True:
            try:
                if channel.recv_ready():
                    data = channel.recv(4096)
                    buffer += data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
                    clean = _strip_ansi(buffer)
                    for prompt in prompts:
                        if prompt in clean:
                            return True
                time.sleep(0.1)
            except socket.timeout:
                pass
            if time.time() >= deadline:
                return False

    def _send_init_command(self, channel, cmd: str, wait_prompt: bool = True) -> bool:
        channel.send(cmd + "\n")
        if not wait_prompt:
            return True
        return self._wait_channel_prompt(channel)

    async def _run_sol_commands(self, reader, writer, target, commands, on_result):
        results = []
        for cmd in commands:
            result = await self._run_telnet_command(reader, writer, target, cmd)
            results.append(result)
            try:
                on_result(result)
            except Exception:
                pass
        return results

    async def _login_sol(self, reader, writer, target: ConnectionTarget) -> bool:
        import re
        import socket
        await self._read_until(reader, ["to exit.]"], timeout=self.command_timeout)
        await asyncio.sleep(0.2)
        SUCCESS_PROMPTS = [">", "]", "#"]
        output = ""

        for try_count in range(3):
            writer.write("\n")
            await writer.drain()

            try:
                output = await self._read_until(
                    reader,
                    ["Please Press ENTER", "Username:", "Password:"] + SUCCESS_PROMPTS,
                    timeout=3.0,
                )
                if any(k in output for k in ["Username:", "Password:", "ENTER"] + SUCCESS_PROMPTS):
                    break
            except (asyncio.TimeoutError, socket.timeout):
                continue

        if "Please Press ENTER" in output:
            writer.write("\n")
            await writer.drain()
            output = await self._read_until(
                reader, ["Username:", "Password:"] + SUCCESS_PROMPTS, timeout=self.command_timeout
            )

        if re.search(r"<[^>]+>", output) or re.search(r"\[[^\]]+\]", output) or "#" in output:
            return True

        if "Username:" in output:
            return await self._try_huawei_login(reader, writer, target, expect_prompts=SUCCESS_PROMPTS)

        if "Password:" not in output:
            return False
        return await self._try_password_login(reader, writer, target, expect_prompts=SUCCESS_PROMPTS)
