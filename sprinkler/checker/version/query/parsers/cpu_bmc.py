"""CPU 型 BMC 的 Redfish 解析策略。

读取 ``RedfishReader`` 提供的 Redfish 资源，提取 CPU 型 BMC 的版本字段：

- iBMC 固件版本来自 ``/redfish/v1/SystemOverview`` 的 ``Managers[0]``。
- BIOS 版本来自 ``/redfish/v1/SystemOverview`` 的 ``Systems[0]``。
- CPU 主板 CPLD / CSR 来自 ``/redfish/v1/Chassis/1/Boards`` 下名为
  ``BCU1CpuBoard1`` 的板卡成员。
- 扩展板 CPLD / CSR 来自 Boards 下名为 ``EXU1ExpBoard1`` 的板卡成员。

某子资源不可用（返回 None / 缺字段）时对应字段保持空串，不抛异常，
保证单点异常不拖垮整个查询流程
"""

from sprinkler.checker.version.query.base import Parser, RawResult
from sprinkler.checker.version.query.parsers.bmc import BmcVersionInfo

# Redfish 子资源路径常量（相对 reader 的根地址拼接）。
# /Chassis/1/Boards：板卡成员清单，按成员 @odata.id 识别 CPU 板 / 扩展板。
_PATH_CHASSIS_BOARDS = "/redfish/v1/Chassis/1/Boards"


class CpuBmcParser(Parser):
    """把 CPU 型 BMC 的 Redfish 资源解析为版本字段。"""

    CPU_BMC = "CPU"

    def parse(self, raw: RawResult) -> tuple:
        """解析一次采集：返回 (字段字典, 成功标志, 原因)。

        ``raw.data`` 应为 ``RedfishReader``；``raw.target.host`` 回填 BMC IP
        CPU BMC 只要拿到 iBMC / BIOS 之一即视为采集成功（ok=True），
        其余字段尽力而为、缺失留空
        """
        reader = raw.data
        ip = raw.target.host or ""
        info = self._collect(reader, ip)
        # 识别成功但核心字段（iBMC / BIOS）全部为空 ≠ 成功：可能是子资源
        # 缺失或结构不符，此时降级失败并指明原因，避免"全空白"行被误判 PASS
        ok = bool(info.active_ibmc or info.bios_version)
        reason = "" if ok else "no version fields collected"
        return info.to_dict(), ok, reason

    def _collect(self, reader, ip: str) -> BmcVersionInfo:
        """按 CPU 型 BMC 的资源布局采集版本字段。"""
        info = BmcVersionInfo(bmc_type=self.CPU_BMC, ip=ip, active_ibmc="", npu_firmware="")

        # 从 SystemOverview 取 iBMC(FirmwareVersion) 与 BIOS(BiosVersion)
        system_overview, _ = reader.get("/redfish/v1/SystemOverview")
        if system_overview:
            managers = system_overview.get("Managers") or []
            if managers:
                info.active_ibmc = str(managers[0].get("FirmwareVersion", ""))
            systems = system_overview.get("Systems") or []
            if systems:
                info.bios_version = str(systems[0].get("BiosVersion", ""))

        # 遍历板卡清单，按 @odata.id 识别 CPU 板与扩展板，取其 CPLD/CSR 版本
        boards, _ = reader.get(_PATH_CHASSIS_BOARDS)
        if boards and "Members" in boards:
            for member in boards["Members"]:
                board_path = str(member.get("@odata.id", ""))
                if "BCU1CpuBoard1" in board_path:
                    board_data, _ = reader.get(board_path)
                    if board_data:
                        info.cpu_board_cpld = str(board_data.get("CPLDVersion", "") or "")
                        info.cpu_board_csr = str(board_data.get("CSRVersion", "") or "")
                elif "EXU1ExpBoard1" in board_path:
                    board_data, _ = reader.get(board_path)
                    if board_data:
                        info.exp_cpld = str(board_data.get("CPLDVersion", "") or "")
                        info.exp_csr = str(board_data.get("CSRVersion", "") or "")
        return info
