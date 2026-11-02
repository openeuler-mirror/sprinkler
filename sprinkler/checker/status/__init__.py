"""Sprinkler checker 模块 —— 设备状态巡检（Status Query）。

通过 BMC Redfish 批量巡检接入设备的运行状态，产出"设备状态巡检表"
（每 BMC 一行：在线 / BMC 健康 / 电源状态 / 功率 / UID / 型号 / 序列号 /
事件告警 / 固件），对应 PoDM ``/UI/Rest/Maintenance/DoManageInfo`` 中可由
BMC Redfish 直接获取的部分（去掉 RackD 私有纳管字段）。

复用版本查询的通道骨架（``RedfishChannel`` / ``RedfishReader`` / 目标收集
与去重），解析层为新增的 ``DeviceStatusParser``；CPU 与 NPU 型 BMC 的固件
口径与版本查询一致（复用 ``CpuBmcParser`` / ``NpuBmcParser``）。

入口：

- CLI：``sprinkler -status-query <目标参数> [选项]``，即
  ``status_query_execute(args)``，结果写入 results.json / results.csv。
- 复用：``collect_status(targets, ...)`` 对给定目标列表直接巡检（供
  Streamlit 页面调用）。
"""

from sprinkler.checker.status.parsers import DeviceStatusInfo, DeviceStatusParser
from sprinkler.checker.status.status_query import (
    collect_status,
    status_query_execute,
)

__all__ = [
    "DeviceStatusInfo",
    "DeviceStatusParser",
    "collect_status",
    "status_query_execute",
]
