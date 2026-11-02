"""SSH CLI 降级升级通道 —— 通过 BMC SSH exec_command 执行 ipmcset 升级。

复用 SSHBMCConnector 建立 BMC SSH 连接（exec_command 模式），
复用 SFTPBMCConnector 上传固件包到 /tmp，
通过 SSH 命令序列执行 ipmcset -d upgrade。

openUBMC 的 ipmcset 不在 exec_command 的默认 PATH 中，必须用
``source /etc/profile`` 加载环境变量后通过完整路径调用 lua 脚本：
``/opt/bmc/skynet/lua /opt/bmc/apps/cli/service/ipmcset.lua``。
升级命令会交互式提示确认，用 ``echo y |`` 管道自动确认。

适用组件：ibmc / bios / cpld / csr / npu_firmware / telnet_crack
不适用组件：cc / cfg（UBM 交换芯片组件走 SOL 或前台 SSH）
"""

import time
from pathlib import Path

from sprinkler.connect.ssh.bmc import SSHBMCConnector
from sprinkler.connect.sftp.bmc import SFTPBMCConnector
from sprinkler.connect.base import ResultProcessor
from sprinkler.connect.targets import ConnectionTarget
from sprinkler.checker.upgrade.channels.base import UpgradeChannel, UpgradeResult

# openUBMC exec_command 下 ipmcset 的完整调用前缀
# - source /etc/profile: 加载 PATH 环境变量
# - echo y |: 自动确认升级提示
# - /opt/bmc/skynet/lua /opt/bmc/apps/cli/service/ipmcset.lua: 完整路径调用
_IPMCSET_PREFIX = "source /etc/profile; echo y | /opt/bmc/skynet/lua /opt/bmc/apps/cli/service/ipmcset.lua"


class SshCliUpgradeChannel(UpgradeChannel):
    """SSH CLI 降级升级通道。"""

    # 各组件的 ipmcset 升级命令模板
    # {filename} = 远程固件包文件名（如 Atlas800-iBMC-26.02.04.01.hpm）
    UPGRADE_COMMANDS = {
        "ibmc":         f"{_IPMCSET_PREFIX} -d upgrade -v /tmp/{{filename}}",
        "bios":         f"{_IPMCSET_PREFIX} -d upgrade -v /tmp/{{filename}}",
        "cpld":         f"{_IPMCSET_PREFIX} -d upgrade -v /tmp/{{filename}}",
        "csr":          f"{_IPMCSET_PREFIX} -d upgrade -v /tmp/{{filename}}",
        "npu_firmware": f"{_IPMCSET_PREFIX} -d upgrade -v /tmp/{{filename}}",
        "telnet_crack": f"{_IPMCSET_PREFIX} -d upgrade -v /tmp/{{filename}}",
    }

    # 升级成功提示关键字（在命令输出中匹配）
    SUCCESS_PATTERNS = ("successfully", "upgrade success", "Success")
    # 升级失败提示关键字
    FAILURE_PATTERNS = ("failed", "error", "Failure")

    def __init__(self, timeout: int = 1800):
        self.timeout = timeout

    def upgrade(self, target, firmware_path: str, component: str,
                timeout: int = 1800, **kwargs) -> UpgradeResult:
        """SSH CLI 降级升级流程。"""
        start = time.time()
        host = self._extract_host(target)
        result = UpgradeResult(ok=False, host=host, component=component, channel="ssh_cli")

        if component not in self.UPGRADE_COMMANDS:
            result.error = f"component {component} not supported by ssh_cli channel"
            result.elapsed = time.time() - start
            return result

        conn_target = ConnectionTarget.from_value(target)

        # 1. SFTP 上传固件包到 BMC /tmp
        #    connect() 成功返回 True，失败抛 RuntimeError（由 except 捕获）
        try:
            sftp = SFTPBMCConnector(
                args=None, result_processor=ResultProcessor(), run_log=None,
            )
            sftp.connect(conn_target)
            try:
                filename = Path(firmware_path).name
                uploaded = sftp.upload_package(firmware_path)
                if not uploaded:
                    result.error = "SFTP upload failed"
                    result.elapsed = time.time() - start
                    return result
            finally:
                sftp.disconnect()
        except Exception as exc:
            result.error = f"SFTP upload error: {exc}"
            result.elapsed = time.time() - start
            return result

        # 2. SSH 执行升级命令
        upgrade_cmd = self.UPGRADE_COMMANDS[component].format(filename=filename)
        try:
            ssh = SSHBMCConnector(
                args=None, result_processor=ResultProcessor(), run_log=None,
            )
            batch = ssh.run_target(conn_target, [upgrade_cmd])
            output = ""
            for item in batch:
                output += str(item.get("stdout", "")) + str(item.get("stderr", ""))

            # 3. 判断升级结果
            output_lower = output.lower()
            if any(p.lower() in output_lower for p in self.SUCCESS_PATTERNS):
                result.ok = True
                result.task_state = "Completed"
            elif any(p.lower() in output_lower for p in self.FAILURE_PATTERNS):
                result.error = f"upgrade command reported failure: {output.strip()[:200]}"
                result.task_state = "Exception"
            else:
                result.error = f"unexpected output: {output.strip()[:200]}"
                result.task_state = "Unknown"
        except Exception as exc:
            result.error = f"SSH upgrade error: {exc}"
        finally:
            result.elapsed = time.time() - start
        return result

    def _extract_host(self, target) -> str:
        if hasattr(target, "host"):
            return str(getattr(target, "host", "") or "")
        return str(target.get("host") or target.get("ip") or "")
