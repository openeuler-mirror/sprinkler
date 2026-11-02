"""版本查询注册表 —— 通道、设备、字段的单一事实来源。

数据驱动替代 version_query.py / report.py 中原本散落的硬编码常量集合。
新增通道 / 设备 / 字段时只需在本模块注册一处，查询分发、组件过滤、
dry-run、hybrid 合并、报告列、合规检查全部自动生效（OCP：对扩展开放，
对修改关闭）

三类元数据：

- ``FieldSpec``：一个版本字段（结果行中的键）→ 报告列标题 + 所属组件组
- ``ChannelSpec``：一个查询通道（redfish / ssh / frontend）→ mode key +
  组件组集合 + 覆盖的目标类型
- ``DeviceSpec``：一个设备目标类型 → 所属通道 + hybrid 合并规则 +
  字段重命名映射 + BMC 类型与识别模式 + 解析器 / 连接类型绑定

扩展指南：

- 新增查询**通道**（如 IPMI）：新增 ``ChannelSpec``，并在 factory.py 的
  ``CHANNEL_CLASSES`` 注册对应的通道类
- 新增**设备目标类型**：新增 ``DeviceSpec``（须引用已存在的 ``channel``，
  并指定 ``parser`` 解析器标识；命令型通道还需 ``connection_type``）
- 新增**版本字段**：新增 ``FieldSpec``，报告列自动出现；Compliance 无需改动
- 新增 **Redfish BMC 类型**：在对应 ``DeviceSpec.bmc_patterns`` 追加产品名
  子串，并在 factory.py 的 ``PARSER_CLASSES`` 注册对应的解析类（``parser`` 字段）
"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class FieldSpec:
    """版本字段（结果列）的声明式元数据。

    每个 FieldSpec 声明版本查询结果中的一个字段 / 报告列：
    - key: 结果字典中的字段名（同时作为 ``-expected-versions`` 清单的 key）。
    - label: 报告表头显示名（HTML 报告 / CSV 列头渲染用）。
    - component: 所属组件组（BMC/BIOS/CPLD/CSR/NPU/OS/CC/CFG），供组件归类
      与 ``-component-filter`` 过滤使用；通用字段（target/ip/type/ok/reason）
      留空。
    """

    key: str                         # 结果字典字段名（结果行 / manifest 的 key）
    label: str                       # 报告列标题（HTML/CSV 渲染用）
    component: str = ""              # 所属组件组（可为空，表示通用字段）


@dataclass(frozen=True)
class ChannelSpec:
    """查询通道（一种传输协议 / 管理模式）。

    - mode_key: 模式键（redfish / ssh / frontend），也是 ``-query-mode`` 的取值。
    - label: 通道显示名（用于日志与 dry-run 输出）。
    - components: 该通道能查询的组件组（供 ``-component-filter`` 判定）。
    - target_types: 该通道覆盖的设备目标类型（供目标收集与汇编）。
    """

    mode_key: str            # "redfish" / "ssh" / "frontend"
    label: str               # "BMC" / "OS" / "frontend"
    components: Tuple[str, ...]   # 该通道能查询的组件组（供 -component-filter 判定）
    target_types: Tuple[str, ...] # 该通道覆盖的设备目标类型


@dataclass(frozen=True)
class DeviceSpec:
    """设备目标类型及其在 hybrid 模式下的合并方式。

    扩展指南：新增解析器时在 factory.py 的 ``PARSER_CLASSES`` 注册解析器标识
    （``parser`` 字段），并在本表登记对应的 ``DeviceSpec``。

    字段说明：
    - target_type: 目标类型键（cpu_bmc / cpu_os / switch / ...）。
    - channel: 所属通道 mode_key（redfish / ssh / frontend）。
    - merge_into_bmc: True 表示该行在 hybrid 合并时并入同 IP 的 BMC 基准行；
      False（如 switch）保持独立行。
    - field_map: 内部字段名 -> 报告字段名的映射（((src, dst), ...)），用于
      hybrid 合并时把窄用字段重命名为独立报告列。
    - bmc_type: Redfish 类型（CPU / NPU），仅 redfish 通道设备使用。
    - bmc_patterns: 有序产品名子串，用于 BMC 类型识别（首匹配优先）。
    - parser: 解析策略标识（factory 映射到 Parser 类）。
    - connection_type: 命令通道的连接类型（create_runner 的 key）。

    扩展指南：1213 与 1260 F 前台交换机当前查询内容 / 命令完全相同，因此
    合并为一个 ``switch`` 目标类型；若将来某一型号出现分歧，可在此登记更多
    DeviceSpec（独立 target_type / connection_type / parser），无需改动其它层。
    """

    target_type: str            # "cpu_bmc" / "cpu_os" / "switch" / ...
    channel: str                # 所属通道 mode_key（redfish / ssh / frontend）
    merge_into_bmc: bool = False  # True：该行按 IP 合并进 BMC 基准行（还原内部字段）
    field_map: Tuple[Tuple[str, str], ...] = ()  # 内部字段名 → 目标字段名（((src, dst), ...)）
    bmc_type: str = ""             # Redfish 类型：CPU / NPU（仅 redfish 通道用）
    bmc_patterns: Tuple[str, ...] = ()  # 产品名子串（有序，用于 BMC 类型识别）
    parser: str = ""               # 解析策略标识（factory 映射到 Parser 类）
    connection_type: str = ""      # 命令通道的连接类型（create_runner 的 key）


# ---------------------------------------------------------------------------
# 通道注册表
# ---------------------------------------------------------------------------
# 说明：redfish 通道不包含 sfu_bmc —— SFU 型 BMC 无产品名识别模式
# （_identify_bmc_type 会返回 UNKNOWN）；SFU 前台交换机的版本由 frontend
# （switch）通道直接 SSH 查询。
CHANNELS: Tuple[ChannelSpec, ...] = (
    ChannelSpec(
        mode_key="redfish", label="BMC",
        components=("BMC", "BIOS", "CPLD", "CSR", "NPU"),
        target_types=("cpu_bmc", "npu_bmc"),
    ),
    ChannelSpec(
        mode_key="ssh", label="OS",
        components=("OS",),
        target_types=("cpu_os",),
    ),
    ChannelSpec(
        mode_key="frontend", label="frontend",
        components=("CC", "CFG"),
        target_types=("switch",),
    ),
)

# ---------------------------------------------------------------------------
# 设备注册表
# ---------------------------------------------------------------------------
# 一个 DeviceSpec 描述一种"目标类型"：它属于哪个通道，以及在 hybrid 合并下
# 是作为基准行（merge_into_bmc=True 的 redfish BMC）还是要并入基准行
# （merge_into_bmc=True 的 OS），或保持独立行（switch）。
# field_map 给出"内部字段名 -> 报告字段名"的映射，用于合并时把窄用字段重命名。
DEVICES: Tuple[DeviceSpec, ...] = (
    # Redfish BMC（hybrid 中的基准行）。
    DeviceSpec(
        target_type="cpu_bmc", channel="redfish", merge_into_bmc=True,
        bmc_type="CPU", bmc_patterns=("950", "CPU", "Atlas 800"),
        parser="cpu_bmc",
    ),
    DeviceSpec(
        target_type="npu_bmc", channel="redfish", merge_into_bmc=True,
        bmc_type="NPU", bmc_patterns=("A5", "Atlas 900"),
        parser="npu_bmc",
    ),
    # 虚拟设备（非真实目标类型）：仅用于向 bmc_identification_patterns() 贡献
    # 900A/900B 这两个也识别为 CPU 的附加产品名子串。它不是查询目标，也不会被
    # TargetContext 收集（target_types_by_channel 只看 CHANNELS）。
    DeviceSpec(
        target_type="900x_cpu", channel="redfish", merge_into_bmc=True,
        bmc_type="CPU", bmc_patterns=("900A", "900B"),
        parser="cpu_bmc",
    ),
    # OS（SSH）—— 字段原样拷贝进基准行（identity 映射）。
    DeviceSpec(
        target_type="cpu_os", channel="ssh", merge_into_bmc=True,
        field_map=(("os_version", "os_version"),
                   ("npu_firmware", "npu_firmware"),
                   ("hdk_version", "hdk_version")),
        parser="os", connection_type="ssh_os",
    ),
    # 前台交换机（switch，SSH 22 直连）—— 1213（CPU/NPU）与 1260 F（SFU）
    # 的查询内容与命令完全相同，统一为一个目标类型。其前台管理 IP 独立于 BMC，
    # 因此 hybrid 下保持单独行（不并入 BMC 行），内部 cc_version/cfg_version
    # 重命名为独立报告列 switch_cc/switch_cfg。
    DeviceSpec(
        target_type="switch", channel="frontend", merge_into_bmc=False,
        field_map=(("cc_version", "switch_cc"), ("cfg_version", "switch_cfg")),
        parser="frontend", connection_type="ssh_switch",
    ),
)

# ---------------------------------------------------------------------------
# 字段注册表（报告列顺序）
# ---------------------------------------------------------------------------
# 每个 FieldSpec(key, label, component) 声明一个结果字段：
# key 为结果字典字段名，label 为报告表头，component 为组件组（通用字段留空）。
# 条目顺序即 HTML 报告的列顺序（见 REPORT_COLUMNS）。
FIELDS: Tuple[FieldSpec, ...] = (
    FieldSpec("target", "Target"),
    FieldSpec("ip", "IP"),
    FieldSpec("type", "Type"),
    FieldSpec("ok", "Status"),
    FieldSpec("bmc_type", "BMC Type", "BMC"),
    FieldSpec("active_ibmc", "iBMC", "BMC"),
    FieldSpec("bios_version", "BIOS", "BIOS"),
    FieldSpec("npu_firmware", "NPU Firmware", "NPU"),
    FieldSpec("npu_csr", "NPU CSR", "CSR"),
    FieldSpec("exp_cpld", "Exp CPLD", "CPLD"),
    FieldSpec("exp_csr", "Exp CSR", "CSR"),
    FieldSpec("cpu_board_cpld", "CPU Board CPLD", "CPLD"),
    FieldSpec("cpu_board_csr", "CPU Board CSR", "CSR"),
    FieldSpec("os_version", "OS Version", "OS"),
    FieldSpec("hdk_version", "HDK Version", "OS"),
    FieldSpec("switch_cc", "Switch CC", "CC"),
    FieldSpec("switch_cfg", "Switch CFG", "CFG"),
    FieldSpec("reason", "Reason"),
)

# 报告列定义：由 FIELDS 扁平化为 (列key, 列标题) 元组有序列表，供
# VersionReportBuilder 生成表头 / 逐行取值，并支持"自动隐藏全空列"。
REPORT_COLUMNS = tuple((f.key, f.label) for f in FIELDS)


# ---------------------------------------------------------------------------
# 查找辅助函数
# 这些纯函数把注册表查成可用的映射/元组，避免各调用方直接依赖注册表内部结构
# （增删注册项后查询接口保持不变）。
# ---------------------------------------------------------------------------
def channel_for_mode(mode_key: str) -> Optional[ChannelSpec]:
    """按 mode_key 查找通道；未注册时返回 None。"""
    for c in CHANNELS:
        if c.mode_key == mode_key:
            return c
    return None


def components_by_channel() -> Dict[str, set]:
    """返回 ``{mode_key: 组件的 set}``，供 -component-filter 通道级判定。"""
    return {c.mode_key: set(c.components) for c in CHANNELS}


def target_types_by_channel() -> Dict[str, Tuple[str, ...]]:
    """返回 ``{mode_key: 目标类型元组}``，供收集某通道的目标用。"""
    return {c.mode_key: c.target_types for c in CHANNELS}


def device_for_target_type(target_type: str) -> Optional[DeviceSpec]:
    """按目标类型查找 DeviceSpec；未登记时返回 None。"""
    for d in DEVICES:
        if d.target_type == target_type:
            return d
    return None


def devices_in_channel(channel: str) -> Tuple[DeviceSpec, ...]:
    """返回属于某通道的全部 DeviceSpec（供合并/遍历用）。"""
    return tuple(d for d in DEVICES if d.channel == channel)


def bmc_identification_patterns() -> Tuple[Tuple[str, str], ...]:
    """有序 BMC 产品名识别模式，首个匹配优先。

    由 redfish 通道中带 ``bmc_type`` 与 ``bmc_patterns`` 的 DeviceSpec 平铺而来，
    顺序即注册顺序（先出现的模式优先级更高）。由工厂注入
    ``RedfishChannel`` 用于识别 CPU / NPU 类型。
    """
    return tuple(
        (pattern, d.bmc_type)
        for d in DEVICES
        if d.channel == "redfish" and d.bmc_type and d.bmc_patterns
        for pattern in d.bmc_patterns
    )


def merge_field_map(target_type: str) -> Tuple[Tuple[str, str], ...]:
    """返回某目标类型的字段重命名映射（(src, dst) 元组）；未登记时返回空。

    供 hybrid 合并层把窄用字段（如 cc_version）重命名为报告列字段。
    """
    d = device_for_target_type(target_type)
    return d.field_map if d else ()
