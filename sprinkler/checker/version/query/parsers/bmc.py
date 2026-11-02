"""Redfish BMC 版本信息数据模型。

CPU 与 NPU 型 BMC 的 Redfish 采集结果统一承载在 ``BmcVersionInfo`` 中。
字段集是两类 BMC 报告列的最小公倍集：某设备不支持的数据源保持默认空串，
保证下游报告 / 合规拿到的是结构一致的字典，且绝不因缺字段抛异常。

字段来源分布：

- 共有数据源（SystemOverview / Managers / Boards）：iBMC、NPU CSR。
- CPU 特有数据源：BIOS、CPU 主板 CPLD / CSR、扩展板 CPLD / CSR。
- NPU 特有数据源：NPU 固件（FirmwareInventory 的 NPU* 成员）、
  扩展板 CPLD / CSR（FirmwareInventory / Boards 双重来源）
"""

from dataclasses import dataclass, asdict


@dataclass
class BmcVersionInfo:
    """Redfish BMC 版本信息（对应设计文档 5.13.4.4 的字段集合）。

    字段说明：
    - bmc_type: BMC 类型（CPU / NPU），由通道识别结果填入。
    - ip: BMC IP（回填自查询目标，供报告列 / 后续排查使用）。
    - active_ibmc: iBMC（主动管理控制器）固件版本。
    - npu_firmware: NPU 固件版本（多 NPU 以逗号合并）。
    - npu_csr: NPU 板 CSR（复位状态寄存器组）版本。
    - exp_cpld: 扩展板 CPLD（复杂可编程逻辑器件）版本。
    - exp_csr: 扩展板 CSR 版本。
    - bios_version: BIOS 固件版本（仅 CPU 型 BMC 有该数据源）。
    - cpu_board_cpld: CPU 主板 CPLD 版本。
    - cpu_board_csr: CPU 主板 CSR 版本。

    设备不支持的字段保持默认空串，不抛异常
    """

    bmc_type: str                # BMC 类型（CPU / NPU）
    ip: str                      # BMC IP 地址
    active_ibmc: str             # iBMC 固件版本
    npu_firmware: str            # NPU 固件版本（多 NPU 逗号拼接）
    npu_csr: str = ""            # NPU 板 CSR 版本
    exp_cpld: str = ""           # 扩展板 CPLD 版本
    exp_csr: str = ""            # 扩展板 CSR 版本
    bios_version: str = ""       # BIOS 固件版本（CPU BMC 数据源）
    cpu_board_cpld: str = ""     # CPU 主板 CPLD 版本
    cpu_board_csr: str = ""      # CPU 主板 CSR 版本

    def to_dict(self) -> dict:
        """转换为普通字典（dataclasses.asdict），便于合并进结果行 / 序列化。"""
        return asdict(self)
