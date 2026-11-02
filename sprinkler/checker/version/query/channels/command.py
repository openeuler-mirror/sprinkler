"""命令通道 —— 经框架连接器批量执行只读命令。

通过 ``create_runner`` + ``connection_type`` 在目标上执行一组只读命令，
并把扁平的命令结果按目标标签聚合成每目标一个 ``RawResult``。适用场景：

- ``ssh_os``：SSH 连接到 CPU 的操作系统（OS），执行版本查询命令。
- ``ssh_switch``：SSH 直连前台交换机（switch，1213 / 1260 F）执行
  ``display startup``。
- ``cpu_1213`` / ``npu_1213``：经 BMC SSH + SOL（Serial over LAN）透传
  到前台交换机执行 ``display startup``（升级等场景的既有路径）。

设计说明：

- 连接隔离、重试、并发与凭据组装由 ``ConnectionRunner``（connection_runner）
  提供，本通道只关心"命令级批量采集"，职责单一。
- 成败判定不在此层：单个命令的错误类型是否致命由各解析器判断（例如
  ``error_type == "command"`` 只丢弃该字段、不判整体失败），保持通道语义
  纯粹，解析策略可插拔
"""

from collections.abc import Mapping
from typing import List, Tuple

from sprinkler.base.log import RunLog
from sprinkler.checker.version.query.base import RawResult
from sprinkler.connect.connection_runner import create_runner


class CommandChannel:
    """命令通道：对一批目标执行固定命令集，按目标聚合结果。

    每个目标会执行通道声明的全部命令；单条命令的胜负（ok / error_type /
    stdout / reason）作为原始结果列表放进 ``RawResult.data``，交由解析器
    逐条解读与聚合。
    """

    def __init__(self, connection_type: str, commands: Tuple[str, ...] = (), target_type: str = ""):
        """构造通道。

        参数：
            connection_type: 传给 ``create_runner`` 的连接类型
                （ssh_os / ssh_switch / cpu_1213 / npu_1213），决定目标
                的传输与认证方式
            commands: 要执行的命令列表（解析器已按其 ``commands`` 声明去重）
            target_type: 通道服务的设备目标类型（填进结果行 type 字段）
        """
        self.connection_type = connection_type
        self.commands = tuple(commands or ())
        self.target_type = target_type

    def collect(self, targets, args=None, run_log: RunLog = None, timeout: int = None) -> List[RawResult]:
        """对目标执行全部命令，返回每目标一个 ``RawResult``（data=原始结果列表）。

        ``timeout`` 透传给底层的每次命令执行超时（连接器自行使用或忽略）
        目标无任何命令结果时 data 为空列表，由解析器产出失败行
        （reason='no results'）。单目标失败不影响其它目标
        """
        if not self.commands:
            raise ValueError("command channel requires at least one command")
        runner = create_runner(args=args, run_log=run_log, connection_type=self.connection_type)
        results = runner.run(targets, list(self.commands))
        by_target: dict = {}
        for result in results:
            if not isinstance(result, dict):
                continue
            by_target.setdefault(result.get("target", ""), []).append(result)
        return [
            RawResult(target=t, data=by_target.get(t.label(), []),
                      target_type=self.target_type or self._target_kind(t))
            for t in targets
        ]

    def _target_kind(self, target) -> str:
        """从目标元数据提取类型标签（如 cpu_os/switch），缺省为空串。

        目标 meta 中的 ``kind`` 记录目标数据来源类型，在通道未显式声明
        ``target_type`` 时回填结果行 type 字段
        """
        meta = getattr(target, "meta", None)
        if isinstance(meta, Mapping):
            return str(meta.get("kind", ""))
        return ""
