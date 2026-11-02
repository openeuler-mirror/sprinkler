"""Telnet connection for SFU COM (serial console)."""
import asyncio
import re
import time

from sprinkler.connect.telnet.base import TelnetConnector
from sprinkler.connect.targets import ConnectionTarget


class TelnetCOMConnector(TelnetConnector):
    def _run_commands(self, target: ConnectionTarget, commands: list[str], on_result=None):
        start = time.time()
        try:
            results = asyncio.run(self._session(target, commands, on_result))
        except Exception as exc:
            error_type = self._classify_error(exc)
            return self.result_processor.failed_many(target, commands, error_type, str(exc), time.time() - start, self.backend_name())
        return results

    async def _session(self, target: ConnectionTarget, commands: list[str], on_result=None):
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

        try:
            if not await self._telnet_login(reader, writer, target):
                self.log_fail(f'{label} 连接失败')
                return []
            self.log_info(f'成功连接到 {label}, 开始执行指令')
            results = []
            for cmd in commands:
                result = await self._run_telnet_command(reader, writer, target, cmd)
                results.append(result)
                try:
                    on_result(result)
                except Exception:
                    pass
            self.log_info(f'{label} 指令执行完成')
            return results
        finally:
            try:
                writer.write("quit\r\n")
                await writer.drain()
                await asyncio.sleep(0.5)
                writer.write("\x1d")
                await writer.drain()
                await asyncio.sleep(0.5)
                writer.write("quit\r\n")
                await writer.drain()
                await asyncio.sleep(0.5)
            except Exception:
                pass
            writer.close()
            await writer.wait_closed()

    async def _telnet_login(self, reader, writer, target: ConnectionTarget):
        try:
            passwords = []
            if target.password:
                passwords.append(target.password)
            from sprinkler.utils.password_store import get_default_passwords
            for pwd in get_default_passwords("sfu_com"):
                passwords.append(pwd)
            passwords = list(dict.fromkeys(passwords))

            writer.write("\r\n")
            await writer.drain()

            output = ""
            pw_index = 0
            start_time = time.time()
            login_timeout = self.connect_timeout * 3
            label = self.target_label(target)

            while time.time() - start_time < login_timeout:
                try:
                    chunk = await asyncio.wait_for(reader.read(4096), timeout=0.5)
                    if chunk:
                        output += chunk
                    else:
                        break
                except asyncio.TimeoutError:
                    pass
                if 'ibmc login:' in output.lower():
                    self.log_fail(f'{label} 串口当前处于 iBMC 视图, 请切换到1260前台')
                    return False

                if 'Password:' in output or 'password:' in output:
                    if pw_index < len(passwords):
                        writer.write(passwords[pw_index] + "\r\n")
                        await writer.drain()
                        pw_index += 1
                        await asyncio.sleep(1)
                        output = ""
                        continue
                    else:
                        return False

                if re.search(r'(?:^|\n|\r\n)<[^\r\n>]+>', output) or re.search(r'(?:^|\n|\r\n)\[[^\r\n\]]+\]', output):
                    break

            lines = [line.strip() for line in output.split('\n') if line.strip()]
            last_line = lines[-1] if lines else ""
            is_user_view = re.match(r'^<[^\r\n>]+>$', last_line)
            is_sys_view = re.match(r'^\[[^\r\n\]]+\]$', last_line)

            if not (is_user_view or is_sys_view):
                return False

            if is_sys_view:
                writer.write("return\r\n")
                await writer.drain()
                await asyncio.sleep(1)

            return True
        except Exception:
            return False

    def backend_name(self):
        return "telnet_com"
