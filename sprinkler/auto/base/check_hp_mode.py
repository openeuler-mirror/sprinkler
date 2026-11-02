"""高性能模式检查：CPU频率 + state2/usage"""

from sprinkler.auto.base.check_result import CheckResult
from sprinkler.auto.base.env_check_utils import run_local, run_remote

CPUFREQ_CMD = "cpupower frequency-info"
STATE2_CHECK_CMD = "test -e /sys/devices/system/cpu/cpu0/cpuidle/state2/usage && echo EXISTS || echo MISSING"


def parse(stdout: str, state2_stdout: str) -> CheckResult:
    """解析CPU高性能模式检查结果"""
    if "current CPU frequency: 2.30 GHz" not in stdout:
        return CheckResult("高性能模式", False, "未开启 (当前频率非2.30 GHz)")
    if "EXISTS" in state2_stdout:
        return CheckResult("高性能模式", False, "未开启 (state2/usage文件存在)")
    return CheckResult("高性能模式", True, "已开启 (CPU频率2.30 GHz)")


def check_local(device_count: int = 8) -> CheckResult:
    """本机高性能模式检查"""
    try:
        rc_freq, stdout_freq, stderr_freq = run_local(CPUFREQ_CMD)
        rc_state2, stdout_state2, _ = run_local(STATE2_CHECK_CMD)
    except Exception as exc:
        return CheckResult("高性能模式", False, f"执行异常: {exc}")

    if rc_freq != 0:
        return CheckResult("高性能模式", False, stderr_freq or "执行失败")
    return parse(stdout_freq, stdout_state2)


def check_remote(runner, target, device_count: int = 8) -> CheckResult:
    """远程高性能模式检查"""
    try:
        freq_res = run_remote(runner, target, CPUFREQ_CMD)
        state2_res = run_remote(runner, target, STATE2_CHECK_CMD)
    except Exception as exc:
        return CheckResult("高性能模式", False, f"检查异常: {exc}")

    if freq_res.get("rc", -1) != 0:
        return CheckResult("高性能模式", False, freq_res.get("stderr", "执行失败"))
    return parse(freq_res.get("stdout", ""), state2_res.get("stdout", ""))


