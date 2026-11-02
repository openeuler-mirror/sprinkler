"""升级策略 —— 根据 blade 表字段和可选的动态探测判断设备形态（PoD/Server）。

三级判断（按优先级）：
1. blade.is_server == 1 → Server
2. blade.device_type == "server" → Server
3. 默认 → PoD（Sprinkler 主场景）

动态探测（npu-smi Board ID）作为可选的 Level 2，仅在 blade 表字段
不确定时触发，且需要 CPU OS SSH 连接，开销较大，默认不启用。
"""

from dataclasses import dataclass


@dataclass
class DeviceForm:
    """设备形态判断结果。"""
    is_pod: bool
    is_server: bool
    board_id: str = ""        # npu-smi 探测结果（如 "0x3" / "0x23" / "0x27"）
    detect_level: int = 1     # 判断依据的层级（1=blade字段, 2=npu-smi, 3=默认）


# npu-smi Board ID → 设备形态映射
BOARD_ID_MAP = {
    "0x3": ("pod", True),
    "0x23": ("server", False),
    "0x27": ("server", False),
}


def detect_device_form(blade: dict, probe_func=None) -> DeviceForm:
    """判断设备形态。

    :param blade: blade 表行（dict）
    :param probe_func: 可选的动态探测函数（返回 Board ID 字符串），
                       为 None 时跳过 Level 2
    :return: DeviceForm
    """
    # Level 1: blade 表字段
    if blade.get("is_server"):
        return DeviceForm(is_pod=False, is_server=True, detect_level=1)
    if blade.get("device_type") == "server":
        return DeviceForm(is_pod=False, is_server=True, detect_level=1)

    # Level 2: npu-smi Board ID（可选）
    if probe_func is not None:
        try:
            board_id = probe_func(blade)
            if board_id in BOARD_ID_MAP:
                _, is_pod = BOARD_ID_MAP[board_id]
                return DeviceForm(
                    is_pod=is_pod, is_server=not is_pod,
                    board_id=board_id, detect_level=2,
                )
        except Exception:
            pass  # 探测失败，降级到 Level 3

    # Level 3: 默认 PoD
    return DeviceForm(is_pod=True, is_server=False, detect_level=3)


def recommended_parallel(device_form: DeviceForm) -> int:
    """推荐并发度。"""
    return 8 if device_form.is_pod else 2


def applicable_components(scenario: str) -> list:
    """返回该场景下需要升级的组件列表。

    PoD 和 Server 的组件类型相同，差异在通道选择和并发度（由 selector 和
    recommended_parallel 决定），因此此处不再区分设备形态。
    """
    return ["ibmc", "bios", "cpld", "npu_firmware", "cc", "cfg"]


def needs_sol_for_switch(device_form: DeviceForm, scenario: str) -> bool:
    """是否需要 SOL 通道升级 UBM 交换芯片。

    开局场景下 UBM 后台 IP 尚未配置，前台 SSH 直连不可用，必须通过
    BMC SOL 串口隧道连接 UBM 交换芯片进行初始配置和固件升级。
    PoD 和 Server 开局均需 SOL（Server 开局也需先通过 SOL 配置 UBM 后台 IP）。
    """
    return scenario == "initial"
