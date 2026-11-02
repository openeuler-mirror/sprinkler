"""前台交换机（C平面）SSH 连接器 —— 直连管理 IP 交互式 shell。

用 paramiko SSH（默认 22 端口）直连前台交换机自己的管理 IP（C 平面），
通过 ``invoke_shell()`` 交互式 shell 执行只读命令（如 ``display startup``）。

适用场景：1213（CPU/NPU）与 1260 F（SFU）前台交换机的管理 IP 直连。
后台连接（M 平面，经 BMC SOL 透传）由 ``cpu_1213`` / ``npu_1213`` 连接器处理。

华为等网络设备的 SSHD 不支持 ``exec_command`` 直接执行命令（Channel closed），
因此统一使用 ``invoke_shell`` 交互式 shell 路径。
"""

import time

from sprinkler.connect.ssh.base import SSHConnector
from sprinkler.connect.targets import ConnectionTarget


class SwitchSSHConnector(SSHConnector):
    """前台交换机（C平面）SSH 直连，通过 invoke_shell 交互式 shell 执行命令。"""

    def _run_commands(self, target: ConnectionTarget, commands: list[str], on_result=None):
        try:
            import paramiko  # noqa: F401
        except Exception as exc:
            return self.result_processor.failed_many(
                target, commands, "dependency", f"paramiko unavailable: {exc}", 0.0, self.backend_name()
            )

        client = self._establish_connection(target)
        label = self.target_label(target)
        try:
            results = self._run_via_shell(client, target, commands, on_result)
            if results is not None:
                return results
            return self.result_processor.failed_many(
                target, commands, "init", f"invoke_shell: 设备不支持交互式 shell",
                time.time(), self.backend_name())
        finally:
            client.close()

    def _run_via_shell(self, client, target, commands, on_result) -> list | None:
        """通过 ``invoke_shell`` 交互式 shell 逐条执行命令。"""
        import socket as _socket

        start = time.time()
        try:
            channel = client.invoke_shell()
            channel.settimeout(self.command_timeout)
        except Exception:
            return None

        label = self.target_label(target)
        self.log_info(f'{label} invoke_shell 建立成功')

        if not self._wait_shell_prompt(channel):
            self.log_warn(f'{label} invoke_shell 未检测到提示符')
            try:
                channel.close()
            except Exception:
                pass
            return None

        results = []
        try:
            for cmd in commands:
                channel.send(cmd + "\n")
                output = self._read_shell_output(channel)
                if output is None:
                    results.append(self.result_processor.make(
                        target, cmd, False, 255, "timeout", "", "shell: 读取超时",
                        time.time() - start, self.backend_name()))
                else:
                    rc, stdout, stderr = self._parse_shell_output(cmd, output)
                    results.append(self.result_processor.make(
                        target, cmd, rc == 0, rc, "" if rc == 0 else "command",
                        stdout, stderr, time.time() - start, self.backend_name()))
                if on_result is not None:
                    try:
                        on_result(results[-1])
                    except Exception as exc:
                        self.log_warn(f'{label} on_result 回调失败: {exc}')
        finally:
            try:
                channel.close()
            except Exception:
                pass
        return results

    def _wait_shell_prompt(self, channel) -> bool:
        """等待 shell 初始提示符；超时返回 False。"""
        deadline = time.time() + self.command_timeout
        import socket as _socket
        while time.time() < deadline:
            try:
                if channel.recv_ready():
                    data = channel.recv(4096)
                    buf = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
                    if _has_prompt(buf):
                        return True
                time.sleep(0.1)
            except (_socket.timeout, TimeoutError):
                pass
        return False

    def _read_shell_output(self, channel) -> str | None:
        """读取一条命令的 shell 输出直到提示符出现；超时返回 None。"""
        deadline = time.time() + self.command_timeout
        buffer = ""
        import socket as _socket
        while time.time() < deadline:
            try:
                if channel.recv_ready():
                    data = channel.recv(4096)
                    buffer += data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
                    if _has_prompt(buffer):
                        return buffer
                time.sleep(0.1)
            except (_socket.timeout, TimeoutError):
                pass
        return None

    @staticmethod
    def _parse_shell_output(cmd: str, output: str) -> tuple:
        """解析 shell 输出为 (rc, stdout, stderr)。

        过滤命令回显行和提示符行（如 ``<LQ-1-4>``），剩余输出作为 stdout。
        交互式 shell 中命令无退出码，rc 恒为 0。
        """
        lines = output.splitlines()
        filtered = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped == cmd.strip():
                continue
            if _is_prompt_line(stripped):
                continue
            filtered.append(stripped)
        stdout = "\n".join(filtered)
        return 0, stdout, ""

    def backend_name(self):
        return "ssh_switch"


def _is_prompt_line(line: str) -> bool:
    """判断一行是否为交换机提示符（如 ``<LQ-1-4>``、``[xxxx]``）。"""
    if line.startswith("<") and line.endswith(">"):
        return True
    if line.startswith("[") and line.endswith("]"):
        return True
    if len(line) <= 5 and line.strip() in (">", "#", "]"):
        return True
    return False


def _has_prompt(text: str) -> bool:
    """检测文本末尾是否包含交换机提示符（> / # / ]）。"""
    clean = text.rstrip("\r\n ")
    for prompt in (">", "#", "]"):
        if clean.endswith(prompt):
            return True
    return False
