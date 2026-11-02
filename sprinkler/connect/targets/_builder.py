"""Target builder for creating ConnectionTarget from cabinet data."""

from sprinkler.connect.targets._base import (
    CabinetRef,
    ConnectionTarget,
    _build_meta,
    _build_node_ref,
)


class TargetBuilder:
    def __init__(self, default_user: str, default_password: str):
        self.default_user = default_user
        self.default_password = default_password

    def port_or_default(self, value, default):
        return default if value in ("", None) else value

    def target_name(self, sp: str, cabinet_ref: CabinetRef, node: int, suffix=""):
        return f"s{sp}:c{cabinet_ref.ca}:n{node}{suffix}"

    def cpu_os(self, sp: str, cabinet_ref: CabinetRef, node: int, cpu: dict):
        node_ref = _build_node_ref(sp, cabinet_ref, node)
        return ConnectionTarget(
            name=self.target_name(sp, cabinet_ref, node),
            host=cpu["host"],
            port=self.port_or_default(cpu.get("port"), 22),
            user=cpu.get("user") or self.default_user,
            password=cpu.get("password") or self.default_password,
            meta=_build_meta("cpu_os", node_ref, cpu.get("raw_name", "")),
        )

    def cpu_bmc(self, sp: str, cabinet_ref: CabinetRef, node: int, cpu: dict):
        node_ref = _build_node_ref(sp, cabinet_ref, node)
        return ConnectionTarget(
            name=self.target_name(sp, cabinet_ref, node, "_bmc"),
            host=cpu.get("cpu_bmc_host", ""),
            port=22,
            user="Administrator",
            password=cpu.get("cpu_bmc_password") or self.default_password,
            meta=_build_meta("cpu_bmc", node_ref, cpu.get("raw_name", "")),
        )

    def npu_bmc(self, sp: str, cabinet_ref: CabinetRef, node: int, npu: dict):
        node_ref = _build_node_ref(sp, cabinet_ref, node)
        return ConnectionTarget(
            name=self.target_name(sp, cabinet_ref, node, "_npu_bmc"),
            host=npu.get("bmc_host", ""),
            port=22,
            user="Administrator",
            password=npu.get("bmc_password") or self.default_password,
            meta=_build_meta("npu_bmc", node_ref, npu.get("raw_name", "")),
        )

    def sfu_bmc(self, sp: str, cabinet_ref: CabinetRef, node: int, sfu: dict):
        node_ref = _build_node_ref(sp, cabinet_ref, node)
        return ConnectionTarget(
            name=self.target_name(sp, cabinet_ref, node, "_sfu_bmc"),
            host=sfu.get("bmc_host", ""),
            port=22,
            user="Administrator",
            password=sfu.get("bmc_password") or self.default_password,
            meta=_build_meta("sfu_bmc", node_ref, sfu.get("raw_name", "")),
        )

    def sfu_com(self, sp: str, cabinet_ref: CabinetRef, node: int, sfu: dict):
        node_ref = _build_node_ref(sp, cabinet_ref, node)
        return ConnectionTarget(
            name=self.target_name(sp, cabinet_ref, node, "_sfu_com"),
            host=sfu.get("com_host", ""),
            port=self.port_or_default(sfu.get("com_port"), 23),
            user="root",
            password="",
            meta=_build_meta("sfu_com", node_ref, sfu.get("raw_name", "")),
        )

    def roce_bmc(self, sp: str, cabinet_ref: CabinetRef, node: int, roce: dict):
        node_ref = _build_node_ref(sp, cabinet_ref, node)
        return ConnectionTarget(
            name=self.target_name(sp, cabinet_ref, node, "_roce_bmc"),
            host=roce.get("bmc_host", ""),
            port=22,
            user="Administrator",
            password=roce.get("bmc_password") or self.default_password,
            meta=_build_meta("roce_bmc", node_ref, roce.get("raw_name", "")),
        )

    def roce_com(self, sp: str, cabinet_ref: CabinetRef, node: int, roce: dict):
        node_ref = _build_node_ref(sp, cabinet_ref, node)
        return ConnectionTarget(
            name=self.target_name(sp, cabinet_ref, node, "_roce_com"),
            host=roce.get("com_host", ""),
            port=self.port_or_default(roce.get("com_port"), 23),
            user="root",
            password="",
            meta=_build_meta("roce_com", node_ref, roce.get("raw_name", "")),
        )

    def npu_1213(self, sp: str, cabinet_ref: CabinetRef, node: int, npu: dict):
        node_ref = _build_node_ref(sp, cabinet_ref, node)
        return ConnectionTarget(
            name=self.target_name(sp, cabinet_ref, node, "_npu_1213"),
            host=npu.get("bmc_host", ""),
            port=22,
            user="Administrator",
            password=npu.get("bmc_password") or self.default_password,
            meta=_build_meta("npu_1213", node_ref, npu.get("raw_name", "")),
        )

    def cpu_1213(self, sp: str, cabinet_ref: CabinetRef, node: int, cpu: dict):
        node_ref = _build_node_ref(sp, cabinet_ref, node)
        return ConnectionTarget(
            name=self.target_name(sp, cabinet_ref, node, "_cpu_1213"),
            host=cpu.get("cpu_bmc_host", ""),
            port=22,
            user="Administrator",
            password=cpu.get("cpu_bmc_password") or self.default_password,
            meta=_build_meta("cpu_1213", node_ref, cpu.get("raw_name", "")),
        )

    def smn_os(self, sp: str, cabinet_ref: CabinetRef, node: int, smn: dict):
        node_ref = _build_node_ref(sp, cabinet_ref, node)
        return ConnectionTarget(
            name=self.target_name(sp, cabinet_ref, node, "_smn_os"),
            host=smn.get("host", ""),
            port=self.port_or_default(smn.get("port"), 22),
            user=smn.get("user") or self.default_user,
            password=smn.get("password") or self.default_password,
            meta=_build_meta("smn_os", node_ref, smn.get("raw_name", "")),
        )

    def smn_bmc(self, sp: str, cabinet_ref: CabinetRef, node: int, smn: dict):
        node_ref = _build_node_ref(sp, cabinet_ref, node)
        return ConnectionTarget(
            name=self.target_name(sp, cabinet_ref, node, "_smn_bmc"),
            host=smn.get("host", ""),
            port=self.port_or_default(smn.get("port"), 22),
            user=smn.get("user") or self.default_user,
            password=smn.get("password") or self.default_password,
            meta=_build_meta("smn_bmc", node_ref, smn.get("raw_name", "")),
        )
