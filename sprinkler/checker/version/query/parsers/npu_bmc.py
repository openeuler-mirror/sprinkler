"""NPU 型 BMC 的 Redfish 解析策略。

读取 ``RedfishReader`` 提供的 Redfish 资源，提取 NPU 型 BMC 的版本字段：

- iBMC 固件版本来自 ``/redfish/v1/Managers/1``；若 FirmwareInventory 中存在
  ``ActiveBMC`` 成员，则以其 Version 覆盖（更精确）。
- NPU 固件来自 ``/redfish/v1/UpdateService/FirmwareInventory`` 中以 ``NPU``
  开头的成员（排除 ``NPUVrd*`` 的虚拟 reading 成员），多版本排序后逗号拼接。
- NPU 板 CSR 来自 ``/redfish/v1/Chassis/1/Boards`` 下 ``ACU1NpuBoard1`` 成员。
- 扩展板 CPLD / CSR 优先取 FirmwareInventory 的 ``EXU1ExpBoard1CPLD`` 成员；
  未取到时回退 Board 清单下 ``EXU1ExpBoard1`` 成员，避免重复来源相互覆盖

某子资源不可用（返回 None / 缺字段）时对应字段保持空串，不抛异常，
保证单点异常不拖垮整个查询流程
"""

from sprinkler.checker.version.query.base import Parser, RawResult
from sprinkler.checker.version.query.parsers.bmc import BmcVersionInfo

# Redfish 子资源路径常量（相对 reader 的根地址拼接）。
# /Managers/1：单个管理控制器资源（iBMC 固件版本）。
# /Chassis/1/Boards：板卡成员清单（NPU 板 CSR、扩展板 CPLD/CSR）。
# /UpdateService/FirmwareInventory：固件清单（NPU 固件、ActiveBMC、扩展板 CPLD）。
_PATH_MANAGERS_1 = "/redfish/v1/Managers/1"
_PATH_CHASSIS_BOARDS = "/redfish/v1/Chassis/1/Boards"
_PATH_FIRMWARE_INVENTORY = "/redfish/v1/UpdateService/FirmwareInventory"


class NpuBmcParser(Parser):
    """把 NPU 型 BMC 的 Redfish 资源解析为版本字段。"""

    NPU_BMC = "NPU"

    def parse(self, raw: RawResult) -> tuple:
        """解析一次采集：返回 (字段字典, 成功标志, 原因)。

        ``raw.data`` 应为 ``RedfishReader``；``raw.target.host`` 回填 BMC IP
        NPU BMC 只要拿到 iBMC / NPU 固件之一即视为采集成功（ok=True），
        其余字段尽力而为、缺失留空
        """
        reader = raw.data
        ip = raw.target.host or ""
        info = self._collect(reader, ip)
        # 识别成功但核心字段（iBMC / NPU 固件）全部为空 ≠ 成功：可能是子
        # 资源缺失或结构不符，此时降级失败并指明原因，避免"全空白"行被误判 PASS
        ok = bool(info.active_ibmc or info.npu_firmware)
        reason = "" if ok else "no version fields collected"
        return info.to_dict(), ok, reason

    def _collect(self, reader, ip: str) -> BmcVersionInfo:
        """按 NPU 型 BMC 的资源布局采集版本字段。"""
        info = BmcVersionInfo(bmc_type=self.NPU_BMC, ip=ip, active_ibmc="", npu_firmware="")

        # 方式一：从 /Managers/1 取 iBMC 版本
        managers, _ = reader.get(_PATH_MANAGERS_1)
        if managers:
            info.active_ibmc = str(managers.get("FirmwareVersion", ""))

        # 遍历固件清单：按成员末尾 id 分发到 NPU* / ActiveBMC / EXU1ExpBoard1CPLD
        firmware_inv, _ = reader.get(_PATH_FIRMWARE_INVENTORY)
        if firmware_inv and "Members" in firmware_inv:
            npu_versions = set()
            for member in firmware_inv["Members"]:
                member_path = str(member.get("@odata.id", ""))
                member_id = self._extract_id_from_path(member_path)
                # NPU 固件成员：id 以 NPU 开头且非 NPUVrd*（虚拟 reading），
                # 聚合版本号到集合，最终排序去重并逗号拼接
                if member_id.startswith("NPU") and not member_id.startswith("NPUVrd"):
                    member_data, _ = reader.get(member_path)
                    if member_data and member_data.get("Version"):
                        npu_versions.add(str(member_data["Version"]))
                # ActiveBMC：更精确的 iBMC 版本，覆盖 /Managers/1 的值
                elif member_id == "ActiveBMC":
                    member_data, _ = reader.get(member_path)
                    if member_data:
                        info.active_ibmc = str(member_data.get("Version", "")) or info.active_ibmc
                # 扩展板 CPLD（优先于 Boards 路径的读取）
                elif member_id == "EXU1ExpBoard1CPLD":
                    member_data, _ = reader.get(member_path)
                    if member_data:
                        info.exp_cpld = str(member_data.get("Version", "")) or info.exp_cpld
            info.npu_firmware = ", ".join(sorted(npu_versions))

        # 板卡清单：ACU1NpuBoard1 提供 NPU CSR；EXU1ExpBoard1 补充扩展板 CPLD/CSR
        boards, _ = reader.get(_PATH_CHASSIS_BOARDS)
        if boards and "Members" in boards:
            for member in boards["Members"]:
                board_path = str(member.get("@odata.id", ""))
                if "/ACU1NpuBoard1" in board_path:
                    board_data, _ = reader.get(board_path)
                    if board_data:
                        info.npu_csr = str(board_data.get("CSRVersion", "") or "")
                elif board_path == "/redfish/v1/Chassis/1/Boards/EXU1ExpBoard1":
                    board_data, _ = reader.get(board_path)
                    if board_data:
                        # 仅当 FirmwareInventory 未提供 CPLD/CSR 时才回填，避免覆盖
                        if not info.exp_cpld:
                            info.exp_cpld = str(board_data.get("CPLDVersion", "") or "")
                        if not info.exp_csr:
                            info.exp_csr = str(board_data.get("CSRVersion", "") or "")
        return info

    @staticmethod
    def _extract_id_from_path(path: str) -> str:
        """提取路径最后一段作为 Redfish 成员 id（例如 '.../NPU0' -> 'NPU0'）。

        固件清单成员 id 是分发（NPU* / ActiveBMC / EXU1ExpBoard1CPLD）的依据，
        因此需要从 @odata.id 中可靠地取出末段作为成员标识
        """
        return path.split("/")[-1]
