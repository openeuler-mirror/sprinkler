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

_ENV_MAP: dict[str, CheckEnv] = {
    "cpu1213": ENV_CPU1213,
    "npu1213": ENV_NPU1213,
    "sfu1260": ENV_SFU1260,
}

_ENV_TYPE_MAP: dict[str, str] = {
    "sfu": "sfu1260",
    "npu": "npu1213",
    "cpu": "cpu1213",
}

ENV_LABEL = {"sfu": "SFU", "npu": "NPU", "cpu": "CPU"}


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
    if getattr(args, 'sfu_check', False):
        return "sfu"
    if getattr(args, 'npu_check', False):
        return "npu"
    if getattr(args, 'cpu_check', False):
        return "cpu"
    return None
