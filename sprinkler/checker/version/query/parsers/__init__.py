"""版本解析策略集合。

解析器（Parser）把通道层产出的某种"原始返回"解析为版本字段字典，并给出
成败判定。每类设备的原始数据形态与字段提取规则不同，故每种设备对应一个
解析策略：

- ``CpuBmcParser`` / ``NpuBmcParser``：把 Redfish JSON（CPU / NPU 型 BMC）
  解析为 iBMC / BIOS / NPU 固件 / CPLD / CSR 等版本字段，见 ``bmc.py``
  的 ``BmcVersionInfo`` 数据模型。
- ``OsParser``：把 SSH 命令输出（/etc/os-release、npu-smi info、HDK
  version.info）解析为 OS 版本 / NPU 固件 / HDK 版本。
- ``FrontendParser``：把前台交换机 ``display startup`` 输出解析为
  CC（启动软件包）/ CFG（启动配置）版本。

解析器的 ``commands`` 类属性声明该设备需要执行哪些命令，供命令通道
（CommandChannel）采集时使用；``parse(raw)`` 接收通道的 ``RawResult``，
返回 ``(字段字典, 是否成功, 原因)`` 三元组。
"""

from sprinkler.checker.version.query.parsers.bmc import BmcVersionInfo
from sprinkler.checker.version.query.parsers.cpu_bmc import CpuBmcParser
from sprinkler.checker.version.query.parsers.npu_bmc import NpuBmcParser
from sprinkler.checker.version.query.parsers.os import OsParser
from sprinkler.checker.version.query.parsers.frontend import FrontendParser

__all__ = [
    "BmcVersionInfo",
    "CpuBmcParser",
    "NpuBmcParser",
    "OsParser",
    "FrontendParser",
]
