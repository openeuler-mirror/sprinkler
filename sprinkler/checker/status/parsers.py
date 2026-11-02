"""BMC 设备状态巡检的 Redfish 解析策略。

把 ``RedfishReader`` 提供的 Redfish 资源解析为"设备状态巡检表"的一行，
对应 RackD ``/UI/Rest/Maintenance/DoManageInfo`` 中可由 BMC Redfish
直接获取（去掉 RackD 私有纳管字段）的部分：

- 在线状态：任一核心资源（Chassis / Systems）可达即在线。
- BMC 健康：``/redfish/v1/Chassis/1``（回退 Systems）的 ``Status.Health``。
- 电源状态：``/redfish/v1/Systems/1`` 的 ``PowerState``。
- 功率：``/redfish/v1/Chassis/1/Power`` 的 ``PowerControl[0].PowerConsumedWatts``。
- UID 状态：``/redfish/v1/Chassis/1`` 的 ``IndicatorLED``。
- 型号 / 序列号：``/redfish/v1/Chassis/1`` 的 ``Model`` / ``SerialNumber``。
- 事件告警条数：``/redfish/v1/Systems/1/LogServices/EventLog/Entries``
  （回退 Chassis）的 ``Members@odata.count``，近似替代 RackD 私有告警计数。
- iBMC / NPU / BIOS 固件：复用 ``NpuBmcParser`` / ``CpuBmcParser`` 现有
  解析逻辑（按识别出的 BMC 类型选择），保证固件口径与版本查询一致。

某子资源不可用（返回 None / 缺字段）时对应字段保持空串，不抛异常，保证
单点异常不拖垮整个巡检流程；只有全部核心资源都不可达时才判整体失败。
"""

from dataclasses import dataclass, asdict

from sprinkler.checker.version.query.base import Parser, RawResult
from sprinkler.checker.version.query.parsers.cpu_bmc import CpuBmcParser
from sprinkler.checker.version.query.parsers.npu_bmc import NpuBmcParser

# Redfish 子资源路径常量（相对 reader 的根地址拼接）。
# /Chassis/1：型号 / 序列号 / UID / BMC 健康。
# /Systems/1：电源状态（回退健康）。
# /Chassis/1/Power：实时功率。
# /Systems/1/LogServices/EventLog/Entries：事件告警条目计数（近似告警数）。
_PATH_CHASSIS_1 = "/redfish/v1/Chassis/1"
_PATH_SYSTEMS_1 = "/redfish/v1/Systems/1"
_PATH_POWER = "/redfish/v1/Chassis/1/Power"
_PATH_SYSTEM_EVENT_ENTRIES = "/redfish/v1/Systems/1/LogServices/EventLog/Entries"
_PATH_CHASSIS_EVENT_ENTRIES = "/redfish/v1/Chassis/1/LogServices/EventLog/Entries"


@dataclass
class DeviceStatusInfo:
    """BMC 设备状态巡检的行数据模型（一行对应一个 BMC）。

    字段说明：
    - bmc_type: BMC 类型（CPU / NPU / UNKNOWN），由通道识别结果填入。
    - ip: BMC IP（回填自查询目标）。
    - online: 在线状态（在线 / 离线），任一核心资源可达即为在线。
    - health: BMC 健康（OK / Warning / Critical，源自 Status.Health）。
    - power_state: 电源状态（如 On / Off）。
    - power_consumed_watts: 实时功率（W）。
    - uid_state: UID 指示灯状态（如 Off / Blinking）。
    - model: 设备型号。
    - serial_number: 序列号。
    - event_count: 事件告警条目数（近似 RackD 私有告警计数）。
    - active_ibmc / npu_firmware / bios_version: 固件版本，交由版本解析器
      按 BMC 类型采集（口径与版本查询一致）。

    设备不支持的数据源保持默认空串，不抛异常。
    """

    bmc_type: str = ""                  # BMC 类型（CPU / NPU / UNKNOWN）
    ip: str = ""                        # BMC IP 地址
    online: str = "离线"                # 在线 / 离线
    health: str = ""                    # BMC 健康（Status.Health）
    power_state: str = ""               # 电源状态（PowerState）
    power_consumed_watts: str = ""      # 实时功率（W）
    uid_state: str = ""                 # UID 指示灯状态
    model: str = ""                     # 设备型号
    serial_number: str = ""             # 序列号
    event_count: str = ""               # 事件告警条目数
    active_ibmc: str = ""               # iBMC 固件版本
    npu_firmware: str = ""              # NPU 固件版本
    bios_version: str = ""              # BIOS 版本（CPU 型数据源）

    def to_dict(self) -> dict:
        """转换为普通字典（dataclasses.asdict），便于合并进结果行 / 序列化。"""
        return asdict(self)


# 按 BMC 类型选择固件版本解析器（口径与版本查询一致）。
_VERSION_PARSERS = {
    "NPU": NpuBmcParser(),
    "CPU": CpuBmcParser(),
}


class DeviceStatusParser(Parser):
    """把 BMC 的 Redfish 资源解析为设备状态巡检行。

    ``raw.data`` 应为 ``RedfishReader``；``raw.device_type`` 提供 CPU / NPU
    类型用于选择固件解析器。全部核心状态资源（Chassis / Systems）均不可达
    时判定失败（离线），否则尽力而为、缺失字段留空。
    """

    def parse(self, raw: RawResult) -> tuple:
        """解析一次采集：返回 (字段字典, 成功标志, 原因)。

        - 通道级错误（连接 / 认证失败）已在策略层命中 raw.error 直接出失败行，
          本解析器不处理。
        - ``raw.device_type`` 为 CPUNPU / UNKNOWN；UNKNOWN 时跳过固件采集。
        """
        reader = raw.data
        ip = raw.target.host or ""
        info = self._collect(raw, reader, ip, raw.device_type)
        fields = info.to_dict()
        if info.online == "在线":
            return fields, True, ""
        return fields, False, "BMC unreachable or no status resource available"

    def _collect(self, raw, reader, ip: str, device_type: str) -> DeviceStatusInfo:
        """按 Redfish 资源布局采集设备状态字段。

        ``raw`` 仅透传给固件解析器（CPU / NPU 解析器按 ``raw.data`` 读资源）。
        """
        info = DeviceStatusInfo(bmc_type=device_type or "", ip=ip)

        # Chassis：型号 / 序列号 / UID / BMC 健康（优先来源）。
        chassis, _ = reader.get(_PATH_CHASSIS_1)
        if chassis:
            info.online = "在线"
            info.model = str(chassis.get("Model", "") or "")
            info.serial_number = str(chassis.get("SerialNumber", "") or "")
            info.uid_state = str(chassis.get("IndicatorLED", "") or "")
            info.health = self._health_of(chassis)

        # Systems：电源状态；Chassis 未提供健康时回退读取。
        system, _ = reader.get(_PATH_SYSTEMS_1)
        if system:
            if info.online == "离线":
                info.online = "在线"
            info.power_state = str(system.get("PowerState", "") or "")
            if not info.health:
                info.health = self._health_of(system)

        # Power：实时功率（存在 PowerControl 成员才取值）。
        power, _ = reader.get(_PATH_POWER)
        if power:
            controls = power.get("PowerControl") or []
            if controls:
                info.power_consumed_watts = str(
                    controls[0].get("PowerConsumedWatts", "") or "")

        # 事件日志：近似告警条数（Systems 优先，Chassis 回退）。
        info.event_count = self._event_count(reader)

        # 固件版本：按识别出的类型复用版本解析器，保证口径一致。
        self._collect_firmware(info, raw, device_type)
        return info

    @staticmethod
    def _health_of(resource: dict) -> str:
        """从资源取 ``Status.Health``（嵌套状态结构），缺失返回空串。"""
        status = resource.get("Status") or {}
        return str(status.get("Health", "") or "")

    def _event_count(self, reader) -> str:
        """统计事件日志条目数；日志服务不可用时返回空串。

        优先 Systems 的事件日志，回退 Chassis；二者都不可用时为空。
        """
        for path in (_PATH_SYSTEM_EVENT_ENTRIES, _PATH_CHASSIS_EVENT_ENTRIES):
            entries, _ = reader.get(path)
            if not entries:
                continue
            count = entries.get("Members@odata.count")
            if count is None:
                members = entries.get("Members")
                count = len(members) if isinstance(members, list) else None
            if count is not None:
                return str(count)
        return ""

    @staticmethod
    def _collect_firmware(info: DeviceStatusInfo, raw: RawResult, device_type: str):
        """按 BMC 类型挑选固件解析器，覆盖 iBMC / NPU / BIOS 字段。

        未知类型（UNKNOWN）跳过固件采集；解析器内部均为尽力而为，异常不外抛。
        """
        parser = _VERSION_PARSERS.get(device_type)
        if parser is None:
            return
        fields, _, _ = parser.parse(raw)
        info.active_ibmc = str(fields.get("active_ibmc", "") or "")
        info.npu_firmware = str(fields.get("npu_firmware", "") or "")
        info.bios_version = str(fields.get("bios_version", "") or "")
