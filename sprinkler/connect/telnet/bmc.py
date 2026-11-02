"""Telnet connection for NPU BMC."""
import asyncio
import time

from sprinkler.connect.telnet.base import TelnetConnector
from sprinkler.connect.targets import ConnectionTarget


class TelnetBMCConnector(TelnetConnector):
    def _run_commands(self, target: ConnectionTarget, commands: list[str], on_result=None):
        start = time.time()
        try:
            results = asyncio.run(self._session(target, commands))
        except Exception as exc:
            error_type = self._classify_error(exc)
            return self.result_processor.failed_many(target, commands, error_type, str(exc), time.time() - start, self.backend_name())
        return results

    async def _session(self, target: ConnectionTarget, commands: list[str]):
        start = time.time()
        label = self.target_label(target)
        try:
            self.log_info(f'正在尝试连接到 {label}')
            reader, writer = await asyncio.wait_for(
                self._establish_connection(target),
                timeout=self.connect_timeout
            )
        except Exception as exc:
            error_type = self._classify_error(exc)
            return self.result_processor.failed_many(target, commands, error_type, str(exc), time.time() - start, self.backend_name())

        self.log_info(f'成功连接到 {label}, 开始执行指令')
        try:
            if not await self._telnet_login(reader, writer, target):
                return self.result_processor.failed_many(target, commands, "auth", "Telnet login failed", time.time() - start, self.backend_name())

            results = []
            for cmd in commands:
                results.append(await self._run_telnet_command(reader, writer, target, cmd))
            self.log_info(f'{label} 指令执行完成')
            return results
        finally:
            writer.close()
            await writer.wait_closed()

    async def _telnet_login(self, reader, writer, target: ConnectionTarget):
        try:
            await asyncio.sleep(1)

            output = ""
            start = time.time()
            while time.time() - start < 2:
                try:
                    chunk = await asyncio.wait_for(reader.read(1024), timeout=0.5)
                    if chunk:
                        output += chunk
                except asyncio.TimeoutError:
                    pass

            if any(p in output for p in ["$ ", "# ", "> "]):
                return True

            writer.write("\n")
            await writer.drain()

            start = time.time()
            while time.time() - start < self.connect_timeout:
                try:
                    chunk = await asyncio.wait_for(reader.read(1024), timeout=0.5)
                    if chunk:
                        output += chunk
                        if any(p in output for p in ["$ ", "# ", "> "]):
                            return True
                except asyncio.TimeoutError:
                    pass
            return False
        except Exception:
            return False

    def backend_name(self):
        return "telnet_bmc"
