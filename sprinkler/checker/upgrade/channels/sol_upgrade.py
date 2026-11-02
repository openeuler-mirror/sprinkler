"""SOL 串口隧道升级通道 —— 通过 BMC SOL 隧道升级 UBM 交换芯片。

复用 SOL1213Connector 的 SOL 连接建立流程（BMC SSH → invoke_shell →
INIT_COMMANDS → SOL 激活 → UBM 登录），在 UBM 交互式 shell 中
执行固件加载命令序列。

适用组件：cc / cfg（UBM 交换芯片固件）
适用场景：开局（initial），UBM 网络 IP 未就绪时
"""

import time
from pathlib import PurePosixPath

from sprinkler.connect.ssh.sol.cpu import CPU1213Connector
from sprinkler.connect.ssh.sol.npu import NPU1213Connector
from sprinkler.connect.base import ResultProcessor
from sprinkler.connect.targets import ConnectionTarget
from sprinkler.checker.upgrade.channels.base import UpgradeChannel, UpgradeResult


class SolUpgradeChannel(UpgradeChannel):
    """SOL 串口隧道升级通道。"""

    # UBM 升级命令序列模板
    # {tftp_server} = TFTP 服务器 IP，{remote_file} = 远程文件名，{local_file} = 本地文件名
    UPGRADE_COMMANDS = {
        "cc": [
            "fsinit put {tftp_server} {remote_file} {local_file}",
            "fsinit setenv bootfile {local_file}",
            "fsinit reset",
        ],
        "cfg": [
            "fsinit put {tftp_server} {remote_file} {local_file}",
            "fsinit setenv cfgfile {local_file}",
            "fsinit reset",
        ],
    }

    # UBM 连接器类型映射
    CONNECTOR_MAP = {
        "cpu": CPU1213Connector,
        "npu": NPU1213Connector,
    }

    def __init__(self, connector_type: str = "cpu", timeout: int = 1800):
        """
        :param connector_type: "cpu" 使用 CPU1213Connector，"npu" 使用 NPU1213Connector（连接 UBM 交换芯片）
        """
        self.connector_type = connector_type
        self.timeout = timeout

    def upgrade(self, target, firmware_path: str, component: str,
                timeout: int = 1800, **kwargs) -> UpgradeResult:
        """SOL 隧道升级流程。"""
        start = time.time()
        host = self._extract_host(target)
        result = UpgradeResult(ok=False, host=host, component=component, channel="sol")

        if component not in self.UPGRADE_COMMANDS:
            result.error = f"component {component} not supported by sol channel"
            result.elapsed = time.time() - start
            return result

        tftp_server = kwargs.get("tftp_server", "")
        if not tftp_server:
            result.error = "sol channel requires tftp_server kwarg"
            result.elapsed = time.time() - start
            return result

        remote_file = kwargs.get("remote_file", PurePosixPath(firmware_path).name)
        local_file = kwargs.get("local_file", PurePosixPath(firmware_path).name)

        commands = [
            cmd.format(
                tftp_server=tftp_server,
                remote_file=remote_file,
                local_file=local_file,
            )
            for cmd in self.UPGRADE_COMMANDS[component]
        ]

        connector_cls = self.CONNECTOR_MAP.get(self.connector_type)
        if connector_cls is None:
            result.error = f"unknown connector_type: {self.connector_type}"
            result.elapsed = time.time() - start
            return result

        try:
            connector = connector_cls(
                args=None, result_processor=ResultProcessor(), run_log=None,
            )
            conn_target = ConnectionTarget.from_value(target)
            batch = connector.run_target(conn_target, commands)

            # 检查输出：reset 后 SOL 连接会断开，属于正常行为（UBM 重启）
            output = ""
            for item in batch:
                output += str(item.get("stdout", "")) + str(item.get("stderr", ""))

            # fsinit reset 会断开 SOL 连接，最后一条命令 ok=False 是预期的
            # 只要 batch[:-1] 全部成功即视为升级完成
            if len(batch) > 1 and all(b.get("ok", False) for b in batch[:-1]):
                result.ok = True
                result.task_state = "Completed"
            elif len(batch) == 1 and batch[0].get("ok", False):
                # 单条命令场景（不应发生于 cc/cfg，但防御性处理）
                result.ok = True
                result.task_state = "Completed"
            else:
                result.error = f"SOL upgrade failed: {output.strip()[:200]}"
                result.task_state = "Exception"
        except Exception as exc:
            result.error = f"SOL upgrade error: {exc}"
        finally:
            result.elapsed = time.time() - start
        return result

    def _extract_host(self, target) -> str:
        if hasattr(target, "host"):
            return str(getattr(target, "host", "") or "")
        return str(target.get("host") or target.get("ip") or "")
