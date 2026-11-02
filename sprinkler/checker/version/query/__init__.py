"""版本查询模块 —— 通过 Redfish + SSH + SOL/Telnet 批量查询设备版本信息。

包结构（按职责划分）：

- ``base.py``            —— 公共模型与模板方法：``RawResult``（采集结果载体）、
  ``Parser``（设备解析策略抽象）、``QueryStrategy``（查询策略模板骨架）。
- ``channels/``          —— 通道策略：``RedfishChannel``（HTTPS + Redfish API，
  附带 BMC 类型识别）与 ``CommandChannel``（SSH/SOL/Telnet 命令批量执行）。
- ``parsers/``           —— 设备解析策略：CPU BMC / NPU BMC / OS / 前台交换机的
  Redfish 或命令输出 -> 版本字段字典。
- ``factory.py``         —— 策略工厂（组合根）：依据 registry 注册表把
  "通道 × 解析器"组装为可执行策略，消除模块间直接依赖。
- ``registry.py``        —— 注册表：通道 / 设备 / 字段的三类声明式元数据
  （单一事实来源，驱动查询分发、组件过滤、dry-run、hybrid 合并、报告与合规）。
- ``version_query.py``   —— ``-version-query`` 主入口：编排各通道策略、结果合并、
  报告生成与合规检查。
- ``report.py``          —— HTML 报告生成器与合规检查器。

对外公共接口：只需从本包导入以下符号即可使用，无需依赖包内具体模块路径：

- 查询策略：``QueryStrategy``（模板基类）、``RedfishQueryStrategy``（Redfish
  BMC 通道策略，识别 CPU/NPU 后自动选择解析器）。
- 通道 / 解析器：``RedfishChannel`` / ``RedfishReader`` / ``CommandChannel``；
  ``OsParser`` / ``CpuBmcParser`` / ``NpuBmcParser`` / ``FrontendParser``。
- 组装入口：``build_strategy``（单通道便捷组装）、``build_channel_strategies``
  （按通道组装全部策略，供编排层使用）。
- 编排与报告：``version_query_execute``（CLI 主入口）、``VersionReportBuilder``
  （HTML 报告）、``ComplianceChecker``（预期版本合规比对）。
- 注册表元数据：``FieldSpec`` / ``ChannelSpec`` / ``DeviceSpec`` 及
  ``CHANNELS`` / ``DEVICES`` / ``FIELDS`` / ``REPORT_COLUMNS``。
"""

from sprinkler.checker.version.query.base import QueryStrategy, RawResult, Parser
from sprinkler.checker.version.query.channels.redfish import (
    RedfishChannel,
    RedfishQueryStrategy,
    RedfishReader,
)
from sprinkler.checker.version.query.channels.command import CommandChannel
from sprinkler.checker.version.query.factory import (
    build_strategy,
    build_channel_strategies,
)
from sprinkler.checker.version.query.parsers.bmc import BmcVersionInfo
from sprinkler.checker.version.query.parsers.os import (
    OsParser, OS_RELEASE_CMD, NPU_SMI_CMD, HDK_VERSION_CMD,
)
from sprinkler.checker.version.query.parsers.cpu_bmc import CpuBmcParser
from sprinkler.checker.version.query.parsers.npu_bmc import NpuBmcParser
from sprinkler.checker.version.query.parsers.frontend import (
    FrontendParser, DISPLAY_STARTUP_CMD,
)
from sprinkler.checker.version.query.report import VersionReportBuilder, ComplianceChecker
from sprinkler.checker.version.query.version_query import version_query_execute
from sprinkler.checker.version.query.registry import (
    FieldSpec, ChannelSpec, DeviceSpec,
    CHANNELS, DEVICES, FIELDS, REPORT_COLUMNS,
)

__all__ = [
    "QueryStrategy",
    "RawResult",
    "Parser",
    "RedfishChannel",
    "RedfishQueryStrategy",
    "RedfishReader",
    "CommandChannel",
    "build_strategy",
    "build_channel_strategies",
    "BmcVersionInfo",
    "CpuBmcParser",
    "NpuBmcParser",
    "OsParser",
    "FrontendParser",
    "VersionReportBuilder",
    "ComplianceChecker",
    "version_query_execute",
    "FieldSpec",
    "ChannelSpec",
    "DeviceSpec",
    "CHANNELS",
    "DEVICES",
    "FIELDS",
    "REPORT_COLUMNS",
]
