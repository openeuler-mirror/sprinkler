"""
时间同步功能 - Python实现
流程：交互/复用代理 → curl 百度连通检测 → 取 HTTP Date 头 → time.clock_settime 同步系统时钟
代理 IP/端口写入 config.conf 复用，连通失败则退出提示用户更换代理。
"""

import email.utils
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from sprinkler.base.log import RunLog
from sprinkler.utils.utils import default_config_dir

URL = "https://www.baidu.com/"

# 代理凭据（与原 shell 版 time_sync.sh 一致）
PROXY_USER = "p_atlas"
PROXY_PASS = "proxy%40123"
NO_PROXY = "localhost,127.0.0.1,*.huawei.com"


def _build_proxy_url(ip: str, port: str) -> str:
    """拼代理 URL：http://p_atlas:proxy%40123@<ip>:<port>"""
    return f"http://{PROXY_USER}:{PROXY_PASS}@{ip}:{port}"


def _apply_proxy_env(ip: str, port: str) -> None:
    """设置代理环境变量（等价 shell 的 export 系列）。"""
    proxy_url = _build_proxy_url(ip, port)
    import os
    os.environ["ip_addr"] = ip
    os.environ["http_proxy"] = proxy_url
    os.environ["https_proxy"] = proxy_url
    os.environ["no_proxy"] = NO_PROXY


def _check_proxy(run_log: RunLog) -> bool:
    """用 curl HEAD 百度做代理连通检测，returncode==0 即可用。"""
    try:
        result = subprocess.run(
            ["curl", "--silent", "--show-error", "--head",
             "--location", "--max-time", "10", "--insecure", URL],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            return True
        err = (result.stderr or result.stdout or "").strip()
        run_log.fail(f"代理连通检测失败: {err or 'curl 返回非零'}")
        return False
    except FileNotFoundError:
        run_log.fail("curl 命令未找到，请检查环境")
        return False
    except subprocess.TimeoutExpired:
        run_log.fail("代理连通检测超时（>10s）")
        return False
    except Exception as exc:
        run_log.fail(f"代理连通检测异常: {exc}")
        return False


def _read_cached_proxy() -> Optional[Tuple[str, str]]:
    """从 config.conf 读 PROXY_IP/PROXY_PORT，两者齐全才返回。"""
    config_file = default_config_dir() / "config.conf"
    if not config_file.exists():
        return None

    values: dict[str, str] = {}
    try:
        with config_file.open("r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    except Exception:
        return None

    ip = values.get("PROXY_IP", "").strip()
    port = values.get("PROXY_PORT", "").strip()
    if ip and port:
        return ip, port
    return None


def _prompt_proxy(run_log: RunLog) -> Optional[Tuple[str, str]]:
    """交互获取代理 IP 与端口，非空且端口为数字才返回。"""
    run_log.info("未找到可用代理配置，请输入代理信息")
    try:
        ip = input("请输入代理IP: ").strip()
        port = input("请输入代理端口: ").strip()
    except (EOFError, KeyboardInterrupt):
        run_log.fail("代理输入被中断")
        return None

    if not ip:
        run_log.fail("代理IP不能为空")
        return None
    if not port or not port.isdigit():
        run_log.fail("代理端口无效（需为数字）")
        return None

    return ip, port


def _save_proxy_to_config(ip: str, port: str) -> None:
    """将 PROXY_IP/PROXY_PORT 写入 config.conf，保留其它已有键（整体读→改→写）。"""
    config_dir = default_config_dir()
    config_file = config_dir / "config.conf"
    config_dir.mkdir(parents=True, exist_ok=True)

    values: dict[str, str] = {}
    if config_file.exists():
        try:
            with config_file.open("r", encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip()
        except Exception:
            pass

    values["PROXY_IP"] = ip
    values["PROXY_PORT"] = port

    with config_file.open("w", encoding="utf-8") as fp:
        for key, value in values.items():
            fp.write(f"{key}={value}\n")


def _resolve_proxy(run_log: RunLog) -> Optional[Tuple[str, str]]:
    """
    解析可用代理 (ip, port)，不可用返回 None。
    优先复用 config.conf 缓存（先连通预检），失败或无缓存则交互输入并校验。
    """
    # 1. 复用缓存
    cached = _read_cached_proxy()
    if cached:
        ip, port = cached
        run_log.info(f"从 config.conf 读到代理: {ip}:{port}，进行连通检测...")
        _apply_proxy_env(ip, port)
        if _check_proxy(run_log):
            run_log.ok(f"代理可用（复用缓存）: {ip}:{port}")
            return ip, port
        run_log.fail("缓存的代理不可用，请重新输入")

    # 2. 交互输入
    pair = _prompt_proxy(run_log)
    if pair is None:
        return None
    ip, port = pair

    _apply_proxy_env(ip, port)
    if not _check_proxy(run_log):
        run_log.fail("代理不可用，请检查代理IP/端口或更换代理后重试")
        return None

    # 3. 连通通过，写入 config.conf 复用
    try:
        _save_proxy_to_config(ip, port)
        run_log.ok(f"代理可用并已写入 config.conf: {ip}:{port}")
    except Exception as exc:
        run_log.fail(f"代理配置写入 config.conf 失败（本次仍继续同步）: {exc}")
    return ip, port


def _get_http_time(insecure: bool) -> float:
    """curl 百度取 HTTP Date 头并转时间戳。"""
    command = [
        "curl", "--silent", "--show-error", "--head",
        "--location", "--max-time", "10",
    ]
    if insecure:
        command.append("--insecure")
    command.append(URL)

    result = subprocess.run(command, capture_output=True, text=True, check=True)

    date_values = []
    for line in result.stdout.splitlines():
        if line.lower().startswith("date:"):
            date_values.append(line.split(":", 1)[1].strip())

    if not date_values:
        raise RuntimeError("HTTP响应中没有Date头")

    http_time = email.utils.parsedate_to_datetime(date_values[-1])
    if http_time.tzinfo is None:
        http_time = http_time.replace(tzinfo=timezone.utc)

    return http_time.timestamp()


def rtc_sync(args):
    """时间同步主入口：配代理 → 连通检测 → 取网络时间 → clock_settime。"""
    run_log = RunLog(args, "Time-sync")

    # 解析并校验代理（不可用则失败退出，不进入同步）
    proxy = _resolve_proxy(run_log)
    if proxy is None:
        run_log.fail("时间同步中止：无可用代理")
        return

    try:
        bootstrap_ts = _get_http_time(insecure=True)
        time.clock_settime(time.CLOCK_REALTIME, bootstrap_ts)

        verified_ts = _get_http_time(insecure=False)
        time.clock_settime(time.CLOCK_REALTIME, verified_ts)

        local_time = datetime.fromtimestamp(verified_ts, timezone.utc).astimezone()
        run_log.ok(f"系统时间已同步: {local_time.strftime('%Y-%m-%d %H:%M:%S')}")
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or "").strip() if exc.stderr else ""
        run_log.fail(f"curl请求失败: {err or exc}")
    except (OSError, RuntimeError, ValueError) as exc:
        run_log.fail(f"时间同步失败: {exc}")

