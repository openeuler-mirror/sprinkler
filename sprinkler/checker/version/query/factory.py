"""版本查询策略工厂 —— 把注册表声明组装为可执行策略。

本模块是策略模式的**组合根（Composition Root）**：连接 ``registry`` 的
声明式元数据与具体的通道 / 解析器实现，消除模块间的直接依赖。新增能力
无需改动编排层（version_query.py）与模板骨架（base.py）。

两个注册表（OCP：新增实现在此登记）：

- ``CHANNEL_CLASSES``：模式键（mode_key，redfish / ssh / frontend）→ 通道策略类。
- ``PARSER_CLASSES``：解析器标识（DeviceSpec.parser）→ 解析策略类。

组装规则（与注册表绑定关系一一对应）：

- **Redfish 通道**：整个 redfish 通道只合成一个 ``RedfishQueryStrategy`` ——
  收集通道登记的全部目标类型（cpu_bmc / npu_bmc，按 host 去重），识别
  CPU / NPU 后经 ``parsers_by_type`` 选择对应解析器。
- **命令通道**（ssh / frontend）：按每个 ``DeviceSpec`` 组装一个
  ``QueryStrategy`` —— 用 ``connection_type`` 构造 ``CommandChannel``、
  以解析器声明的 ``commands`` 批量执行命令，再由对应解析器取值。

约定：工厂不实例化连接 / 会话，不持有任何状态，可安全多次调用（每次``collect``
由通道 / 连接器自行创建与释放资源）
"""

from sprinkler.base.log import RunLog
from sprinkler.checker.version.query.base import QueryStrategy
from sprinkler.checker.version.query.channels.command import CommandChannel
from sprinkler.checker.version.query.channels.redfish import (
    RedfishChannel,
    RedfishQueryStrategy,
)
from sprinkler.checker.version.query.parsers import (
    CpuBmcParser,
    FrontendParser,
    NpuBmcParser,
    OsParser,
)
from sprinkler.checker.version.query.registry import (
    bmc_identification_patterns,
    channel_for_mode,
    devices_in_channel,
    target_types_by_channel,
)
from sprinkler.checker.version.query.switch_targets import collect_switch_targets

# 模式键 -> 通道策略类（OCP：新增通道在此注册）。
CHANNEL_CLASSES = {
    "redfish": RedfishChannel,
    "ssh": CommandChannel,
    "frontend": CommandChannel,
}

# 解析器标识 -> 解析策略类（OCP：新增解析器在此注册）。
PARSER_CLASSES = {
    "cpu_bmc": CpuBmcParser,
    "npu_bmc": NpuBmcParser,
    "os": OsParser,
    "frontend": FrontendParser,
}


def build_strategy(mode_key: str, args=None, run_log: RunLog = None,
                   timeout: int = 30, parallel: int = 1) -> QueryStrategy:
    """按通道模式键组装查询策略；未注册的通道抛 ValueError。

    - 未注册模式键 / 无对应通道类：抛 ``ValueError``。
    - Redfish 通道：返回 ``RedfishQueryStrategy``（一次识别全类型）。
    - 命令通道（ssh / frontend）：返回 ``QueryStrategy``，以该通道登记的
      首个有解析器的目标类型为目标（便捷调用；编排层请用
      ``build_channel_strategies`` 获取全部设备策略）。
    """
    channel_spec = channel_for_mode(mode_key)
    if channel_spec is None:
        raise ValueError(f"channel not registered: {mode_key}")
    channel_class = CHANNEL_CLASSES.get(mode_key)
    if channel_class is None:
        raise ValueError(f"no channel strategy registered for {mode_key!r}")

    if mode_key == "redfish":
        return _build_redfish_strategy(args=args, run_log=run_log,
                                       timeout=timeout, parallel=parallel)
    return _build_command_strategy(mode_key, args=args, run_log=run_log, timeout=timeout)


def _build_redfish_strategy(args=None, run_log: RunLog = None,
                            timeout: int = 30, parallel: int = 1) -> RedfishQueryStrategy:
    """组装整套 Redfish BMC 查询策略（识别 + 按类型选择 CPU/NPU 解析器）。

    从 registry 收集：
    - 目标类型：redfish 通道登记的 ``target_types``（cpu_bmc / npu_bmc）。
    - 识别模式：``bmc_identification_patterns()``（有序、首匹配优先）。
    - 解析器：redfish 通道下每个带 ``bmc_type`` 设备的 Parser（按类型去重）。
    """
    target_types = target_types_by_channel().get("redfish", ())
    parsers_by_type = {}
    for device in devices_in_channel("redfish"):
        parser_cls = PARSER_CLASSES.get(device.parser)
        if device.bmc_type and parser_cls is not None:
            parsers_by_type.setdefault(device.bmc_type, parser_cls())
    channel = RedfishChannel(bmc_patterns=bmc_identification_patterns(),
                             timeout=timeout, parallel=parallel)
    return RedfishQueryStrategy(channel=channel, parsers_by_type=parsers_by_type,
                                target_types=target_types,
                                args=args, run_log=run_log, timeout=timeout)


def _build_command_strategy(mode_key: str, args=None, run_log: RunLog = None,
                            timeout: int = 30) -> QueryStrategy:
    """按命令通道登记的首个有解析器的目标类型组装单策略。

    命令通道按设备目标类型各自组装（每 DeviceSpec 一个 QueryStrategy，
    见 ``build_channel_strategies``）；此处仅用于便捷的单通道调用
    （``build_strategy``），返回首设备策略。
    """
    channel_spec = channel_for_mode(mode_key)
    if channel_spec is None:
        raise ValueError(f"channel not registered: {mode_key}")
    for target_type in channel_spec.target_types:
        device = next((d for d in devices_in_channel(mode_key)
                       if d.target_type == target_type), None)
        if device is not None:
            return _build_device_strategy(device, args=args, run_log=run_log, timeout=timeout)
    raise ValueError(f"no devices registered for channel {mode_key!r}")


def build_channel_strategies(mode_key: str, args=None, run_log: RunLog = None,
                             timeout: int = 30, parallel: int = 1) -> list:
    """组装某通道在给定模式下应执行的全部策略，返回策略列表。

    - Redfish 通道：返回单个 ``RedfishQueryStrategy``（一次识别全类型）。
    - 命令通道：按每个登记的 DeviceSpec 返回一个 ``QueryStrategy``。

    跳过仅供识别模式贡献模式的虚拟设备（如 900x_cpu，其 target_type 不在
    ``target_types_by_channel`` 中，不是查询目标）。
    """
    target_types = set(target_types_by_channel().get(mode_key, ()))
    strategies = []
    for device in devices_in_channel(mode_key):
        if device.target_type not in target_types:
            continue  # 跳过仅供识别模式贡献模式的虚拟设备（如 900x_cpu）
        if device.channel == "redfish":
            continue  # Redfish 通道由 _build_redfish_strategy 统一组装
        strategies.append(_build_device_strategy(device, args=args,
                                                 run_log=run_log, timeout=timeout))
    if mode_key == "redfish":
        return [_build_redfish_strategy(args=args, run_log=run_log,
                                        timeout=timeout, parallel=parallel)]
    return strategies


def _build_device_strategy(device, args=None, run_log: RunLog = None,
                           timeout: int = 30) -> QueryStrategy:
    """按单个 DeviceSpec 组装命令型查询策略。

    用 ``connection_type`` 构造 ``CommandChannel``、以解析器声明的
    ``commands`` 作为待执行命令集；解析器负责取值与成败判定。
    解析器标识未注册时抛 ``ValueError``，在组装期尽早暴露配置错误。
    """
    parser_cls = PARSER_CLASSES.get(device.parser)
    if parser_cls is None:
        raise ValueError(f"no parser registered for {device.parser!r}")
    parser = parser_cls()
    channel = CommandChannel(connection_type=device.connection_type,
                             commands=parser.commands,
                             target_type=device.target_type)
    # switch 目标来源不在 TargetContext 能力范围（跨设备族从 Excel 的
    # frontend_host / com_host 收集），故为其注入 checker 内的收集器。
    target_collector = collect_switch_targets if device.target_type == "switch" else None
    return QueryStrategy(channel=channel, parser=parser,
                         target_type=device.target_type,
                         args=args, run_log=run_log, timeout=timeout,
                         target_collector=target_collector)
