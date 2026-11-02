"""功率检查：通过 drv_hlt_dsmi_test get_rated_power 读取额定功率"""

from pathlib import Path

from sprinkler.auto.base.check_result import CheckResult
from sprinkler.auto.base.env_check_utils import run_local, run_remote

POWER_CMD = "./drv_hlt_dsmi_test get_rated_power 1"


def parse(stdout: str, device_count: int, expected_power_per_device: float = 899.6) -> CheckResult:
    """解析功率检查结果"""
    total_power = 0.0
    for line in stdout.splitlines():
        if "dev_id:" in line and "get_rated_power:" in line:
            parts = line.split()
            for i, part in enumerate(parts):
                if part == "get_rated_power:":
                    try:
                        total_power += float(parts[i + 1]) / 1000
                    except (IndexError, ValueError):
                        pass
                    break

    expected_power = expected_power_per_device * device_count
    if abs(total_power - expected_power) < 0.1:
        return CheckResult("功率检查", True, f"功率正常，总功率: {total_power:.1f} W")
    return CheckResult("功率检查", False, f"功率异常，总功率: {total_power:.1f} W (预期: {expected_power:.1f} W)")


def check_local(test_rate_file: str, device_count: int = 8) -> CheckResult:
    """本机功率检查"""
    work_dir = str(Path(test_rate_file) / "test" / "lib" / "host")
    if not Path(work_dir).exists():
        return CheckResult("功率检查", False, f"工作目录不存在: {work_dir}")
    rc, stdout, stderr = run_local(POWER_CMD, cwd=work_dir)
    if rc != 0:
        return CheckResult("功率检查", False, stderr or "执行失败")
    return parse(stdout, device_count)


def check_remote(runner, target, device_count: int = 8) -> CheckResult:
    """远程功率检查"""
    try:
        res = run_remote(runner, target, POWER_CMD)
        if res.get("rc", -1) != 0:
            return CheckResult("功率检查", False, res.get("stderr", "执行失败"))
        return parse(res.get("stdout", ""), device_count)
    except Exception as exc:
        return CheckResult("功率检查", False, f"检查异常: {exc}")


