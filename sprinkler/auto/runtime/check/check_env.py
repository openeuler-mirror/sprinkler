from dataclasses import dataclass, field

_NPU_ALLOWED_DOWN = r"^400GUB\d+/(2|4|6|8|9|11|13|15)/9$|^MEth0/0/0$"
_CPU_ALLOWED_DOWN = r"^400GUB\d+/(1|2)/9$|^MEth0/0/0$"
_SFU_ALLOWED_DOWN = r"^400GUB\d+/([1-8]/(9|14|23|28)|9/(9|18)|(1[0-2])/(1|9|18))$|^MEth0/0/0$"


@dataclass(frozen=True)
class CheckEnv:
    """环境配置：目标类型、连接方式、芯片数量、slot映射、允许down的端口正则、预期子卡数。"""

    name: str
    target_type: str
    connection_type: str
    chip_count: int
    slot_map: dict[int, int] = field(default_factory=dict)
    allowed_down_pattern: str = r"^MEth0/0/0$"
    expected_card_count: int = 0
    credit_after_lines: int = 18
    # 默认保持原行为：任一命令失败即返回 ExecuteFail；5808 命令多，允许部分失败后继续分析。
    continue_on_partial_failure: bool = False


ENV_CPU1213 = CheckEnv(
    name="cpu1213",
    target_type="cpu_1213",
    connection_type="cpu_1213",
    chip_count=2,
    slot_map={0: 14, 1: 15, 2: 16, 3: 17, 4: 28, 5: 29, 6: 30, 7: 31},
    allowed_down_pattern=_CPU_ALLOWED_DOWN,
    expected_card_count=2,
    credit_after_lines=10,
)

ENV_NPU1213 = CheckEnv(
    name="npu1213",
    target_type="npu_1213",
    connection_type="npu_1213",
    chip_count=16,
    slot_map={0: 18, 1: 19, 2: 20, 3: 21, 4: 23, 5: 24, 6: 25, 7: 26},
    allowed_down_pattern=_NPU_ALLOWED_DOWN,
    expected_card_count=16,
    credit_after_lines=10,
)

ENV_SFU1260 = CheckEnv(
    name="sfu1260",
    target_type="sfu_com",
    connection_type="telnet_com",
    chip_count=20,
    slot_map={0: 61, 1: 62, 2: 63, 3: 64},
    allowed_down_pattern=_SFU_ALLOWED_DOWN,
    expected_card_count=12,
    credit_after_lines=18,
)

ENV_5808 = CheckEnv(
    name="5808",
    target_type="5808_front",
    connection_type="ssh_switch",
    chip_count=0,
    continue_on_partial_failure=True,
)

# 5808 前台交换机端口拓扑（port / lane / credit 检查共用）
PAGINATION_OFF_5808 = "screen-length 0 temporary"
PORT_PREFIX_5808 = "800GUB"
PORT_COUNT_5808 = 128
SUBPORT_COUNT_5808 = 2
EXPECTED_PORT_COUNT_5808 = PORT_COUNT_5808 * SUBPORT_COUNT_5808
EXPECTED_LANE_COUNT_5808 = 4
INTERFACE_TEMPLATE_5808 = "800GUB1/0/{port}:{sub}"


def build_5808_interfaces() -> list[str]:
    return [
        INTERFACE_TEMPLATE_5808.format(port=port, sub=sub)
        for port in range(1, PORT_COUNT_5808 + 1)
        for sub in range(1, SUBPORT_COUNT_5808 + 1)
    ]


def parse_5808_port_status(block: str) -> tuple[set[str], set[str]]:
    """解析 dis int b，返回 (所有 800GUB 端口, PHY/Protocol 不为 up 的端口)。"""
    ports = set()
    down_ports = set()
    for line in block.strip().split("\n"):
        parts = line.split()
        if len(parts) >= 3 and parts[0].startswith(PORT_PREFIX_5808):
            ports.add(parts[0])
            if parts[1] != "up" or parts[2] != "up":
                down_ports.add(parts[0])
    return ports, down_ports


_ENV_MAP: dict[str, CheckEnv] = {
    "cpu1213": ENV_CPU1213,
    "npu1213": ENV_NPU1213,
    "sfu1260": ENV_SFU1260,
    "5808": ENV_5808,
}

_ENV_TYPE_MAP: dict[str, str] = {
    "sfu": "sfu1260",
    "npu": "npu1213",
    "cpu": "cpu1213",
    "5808": "5808",
    # L1/L2 当前共用 5808 检查实现，仅日志标识不同。
    "L1": "5808",
    "L2": "5808",
}

ENV_LABEL = {
    "sfu": "SFU", "npu": "NPU", "cpu": "CPU",
    "L1": "L1", "L2": "L2",
    "5808": "5808",
}


def resolve_env(env_name: str) -> CheckEnv:
    env = _ENV_MAP.get(env_name)
    if env is None:
        raise ValueError(f"不支持的环境: {env_name}，可选: {list(_ENV_MAP.keys())}")
    return env


def resolve_env_type(env_type: str) -> str:
    check_type = _ENV_TYPE_MAP.get(env_type)
    if check_type is None:
        raise ValueError(f"不支持的环境类型: {env_type}，可选: {list(_ENV_TYPE_MAP.keys())}")
    return check_type


def resolve_env_from_args(args) -> str | None:
    if getattr(args, 'L1_check', False):
        return "L1"
    if getattr(args, 'L2_check', False):
        return "L2"
    if getattr(args, 'sfu_check', False):
        return "sfu"
    if getattr(args, 'npu_check', False):
        return "npu"
    if getattr(args, 'cpu_check', False):
        return "cpu"
    return None
