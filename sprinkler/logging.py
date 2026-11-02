# 定义颜色代码
import time


class Colors:
    RED = '\033[31m'
    GREEN = '\033[32m'
    High_GREEN = '\033[92m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


# 根据级别选择颜色
color_map = {
    "INFO": Colors.CYAN,
    "PASS": Colors.High_GREEN,
    "OK": Colors.GREEN,
    "WARN": Colors.YELLOW,
    "FAIL": Colors.RED,
    "DEBUG": Colors.BLUE
}


def get_color_by_level(level):
    return color_map.get(level, Colors.WHITE)


# 定义log函数
def log(level, check_id, message, host=""):
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    # 添加主机标识到日志中
    host_prefix = f"[{host}] " if host else ""

    # 获取对应级别的颜色
    color = get_color_by_level(level)

    # 带颜色的日志输出
    log_message = f"{now} | {color}{level:5}{Colors.RESET} | {check_id} | {host_prefix}{message}"

    print(log_message, flush=True)


def output_auto_details(status: str, check_id: str, message: str, host: str = ""):
    """增加host参数和颜色"""
    log("INFO", check_id, ">>>AUTO_DETAILS_START<<<", host)

    # 根据状态选择颜色
    if status == "OK":
        log("OK", check_id, message, host)
    elif status == "FAIL":
        log("FAIL", check_id, message, host)
    else:
        log("INFO", check_id, message, host)

    log("INFO", check_id, ">>>AUTO_DETAILS_END<<<", host)
