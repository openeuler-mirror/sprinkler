"""版本查询通道策略集合。

通道（Channel）封装一条与设备的通信链路，统一负责把一组目标"采集"为
逐目标的 ``RawResult``（原始结果载体），供上层解析策略（Parser）取值。
不同类型的通信协议各自实现一个通道类：

- ``RedfishChannel``：HTTPS + Redfish API，连接 BMC 并完成设备类型识别
  （CPU / NPU / UNKNOWN）。配套 ``RedfishReader``（单目标只读访问器）与
  ``RedfishQueryStrategy``（识别后按类型选择解析器的查询策略）。
- ``CommandChannel``：经 ``create_runner`` 连接器在目标上批量执行一组
  只读命令；SSH 用于 CPU OS，SOL / Telnet 用于前台交换机。

通道只关心"采集"，不负责"判定"：单命令失败是否致命由解析器决定，从而
保持通道语义纯粹、解析策略可插拔。
"""

from sprinkler.checker.version.query.channels.command import CommandChannel
from sprinkler.checker.version.query.channels.redfish import (
    RedfishChannel,
    RedfishReader,
    RedfishQueryStrategy,
)

__all__ = [
    "CommandChannel",
    "RedfishChannel",
    "RedfishQueryStrategy",
    "RedfishReader",
]
