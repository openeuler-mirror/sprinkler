"""分bin检查：通过 npu-smi info -t board 读取 Board ID"""

import re

from sprinkler.auto.base.check_result import CheckResult
from sprinkler.auto.base.env_check_utils import run_local, run_remote

BOARD_CMD_PREFIX = "npu-smi info -t board -i"


def extract_board_id(line: str) -> str:
    """从 'Board ID : 0x29' 或 'Board ID : NA' 行提取值，排除 Main Board ID 行"""
    if "Main Board" in line:
        return ""
    m = re.search(r'Board ID\s*:\s*(\S+)', line)
    return m.group(1) if m else ""


def parse_device(stdout: str, device_index: int) -> tuple[str, str]:
    """解析单个设备的Board ID，返回 (id_value, detail_str)"""
    for line in stdout.splitlines():
        bid = extract_board_id(line)
        if bid:
            return bid, f"设备{device_index}: {bid}"
    return "", f"设备{device_index}: 无"


def check_local(device_count: int = 8) -> CheckResult:
    """本机分bin检查"""
    return _build_check(
        device_count,
        lambda i: run_local(f"{BOARD_CMD_PREFIX} {i}"),
    )


def check_remote(runner, target, device_count: int = 8) -> CheckResult:
    """远程分bin检查"""
    return _build_check(
        device_count,
        lambda i: (
            lambda res: (res.get("rc", -1), res.get("stdout", ""), res.get("stderr", ""))
        )(run_remote(runner, target, f"{BOARD_CMD_PREFIX} {i}")),
    )


def _build_check(device_count: int, stdout_getter) -> CheckResult:
    """通用分bin检查逻辑，stdout_getter(index) -> (rc, stdout, stderr)"""
    checked_count = 0
    details = []
    has_na = False
    for i in range(device_count):
        rc, stdout, stderr = stdout_getter(i)
        if rc != 0:
            details.append(f"设备{i}: 失败")
            continue
        bid, detail = parse_device(stdout, i)
        checked_count += 1
        details.append(detail)
        if bid.upper() == "NA":
            has_na = True

    all_valid = checked_count == device_count and not has_na
    msg = f"已检查 {checked_count}/{device_count} 个设备 ({'; '.join(details)})"
    return CheckResult("分bin检查", all_valid, msg)


