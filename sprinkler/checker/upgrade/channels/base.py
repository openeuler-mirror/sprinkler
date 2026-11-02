"""升级通道抽象基类 —— 定义统一的升级接口契约。

所有具体升级通道（Redfish / SSH CLI / SOL）继承 ``UpgradeChannel`` 并实现
``upgrade()`` 方法。升级通道只负责"对单个目标执行单个组件的固件升级"，
不管理目标列表的并行调度（由 UpgradeOrchestrator 负责）。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class UpgradeResult:
    """单目标单组件升级结果。

    通道的 ``upgrade()`` 返回此对象，不抛异常——所有失败通过
    ``ok=False`` + ``error`` 字段表达。原因：
    1. 升级是"多目标并行"操作，单目标失败不应中断其他目标
    2. 错误信息需要持久化到 ``upgrade_record.error_msg``，字符串比异常更方便
    3. 与现有 ``RawResult.error`` 模式一致（版本查询也是字符串错误）
    """

    ok: bool = False
    host: str = ""
    component: str = ""
    channel: str = ""           # redfish / ssh_cli / sol
    task_state: str = ""        # Completed / Exception / Timeout / ""
    version_before: str = ""
    version_after: str = ""
    error: str = ""
    elapsed: float = 0.0


class UpgradeChannel(ABC):
    """升级通道接口：对单个目标执行单个组件的固件升级。

    实现约定：
    - ``upgrade()`` 必须是"尽力而为"的：不抛异常，所有失败通过
      ``UpgradeResult.ok=False`` + ``error`` 返回
    - 升级前版本采集和升级后验证在通道内部完成（复用版本查询框架）
    - 通道不管理目标列表的并行调度（由 ``UpgradeOrchestrator`` 负责）
    """

    @abstractmethod
    def upgrade(self, target, firmware_path: str, component: str,
                timeout: int = 1800, **kwargs) -> UpgradeResult:
        """执行升级。

        :param target: ``ConnectionTarget`` 或 dict（须含 host/user/password）
        :param firmware_path: 固件包本地文件路径（SFTP 上传到 BMC 后执行升级）
        :param component: 组件类型（ibmc/bios/cpld/npu_firmware/cc/cfg/telnet_crack）
        :param timeout: 升级总超时秒数
        :return: ``UpgradeResult``
        """
