"""PRBS 前置条件：确保 ``ssh root@npu<rank>`` 能免密登录 NPU OS。

免密由 ``sprinkler -no-pass`` 配置：生成 ``~/.ssh/id_ed25519_ipv6``，为每个
设备在 ``~/.ssh/config`` 写 ``Host npu<rank>`` 别名，并把公钥下发到 NPU OS。
后续 serdestool 命令都通过该别名执行，因此这里使用与实际执行一致的 ssh
参数探测：``BatchMode=yes`` + ``PasswordAuthentication=no``，禁止密码交互，
避免 PRBS 流程卡在密码输入而无人值守。
"""

from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, List, Optional

from sprinkler.logging import Colors

NPU_ALIAS_FORMAT = "npu{}"
DEFAULT_NPU_COUNT = 8
DEFAULT_PROBE_TIMEOUT = 5
DEFAULT_WORKERS = 8

# 与 execute_npu.SSH_OPTIONS / npu_access.NPU_SSH_OPTIONS 保持一致：
# 仅密钥认证，不校验也不会写入 known_hosts。探测与下发共用。
SSH_OPTIONS = (
    "-o", "BatchMode=yes",
    "-o", "PasswordAuthentication=no",
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
)


@dataclass
class ProbeResult:
    """单张卡的免密探测结果。"""

    rank: int
    alias: str
    ok: bool
    reason: str = ""


def default_ranks() -> List[int]:
    """PRBS 覆盖的 NPU rank：优先取 host/device pair，缺失时按 8 卡兜底。"""
    try:
        from sprinkler.config.host_device_pairs import get_host_and_device_pairs
        ranks = sorted(get_host_and_device_pairs().keys())
    except Exception:
        ranks = []
    return ranks or list(range(DEFAULT_NPU_COUNT))


def probe_npu(rank: int, timeout: int = DEFAULT_PROBE_TIMEOUT) -> ProbeResult:
    """用仅密钥方式探测一次 ``ssh root@npu<rank> true``。"""
    alias = NPU_ALIAS_FORMAT.format(rank)
    cmd = ["ssh", *SSH_OPTIONS, "-o", f"ConnectTimeout={timeout}",
           f"root@{alias}", "true"]
    try:
        done = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout + 2)
    except FileNotFoundError:
        return ProbeResult(rank, alias, False, "未找到 ssh 可执行文件")
    except PermissionError:
        return ProbeResult(rank, alias, False, "执行 ssh 权限不足")
    except subprocess.TimeoutExpired:
        return ProbeResult(rank, alias, False, f"SSH 探测超时（{timeout + 2}s）")
    except OSError as exc:
        return ProbeResult(rank, alias, False, f"执行 ssh 失败: {exc}")

    if done.returncode == 0:
        return ProbeResult(rank, alias, True)
    reason = _last_line(done.stderr) or _last_line(done.stdout) or f"rc={done.returncode}"
    return ProbeResult(rank, alias, False, reason)


def check_no_pass(ranks: Optional[List[int]] = None,
                  timeout: int = DEFAULT_PROBE_TIMEOUT) -> List[ProbeResult]:
    """并行探测所有 rank，返回按 rank 排序的结果。"""
    ranks = list(ranks if ranks is not None else default_ranks())
    if not ranks:
        return []

    results: Dict[int, ProbeResult] = {}
    with ThreadPoolExecutor(max_workers=min(DEFAULT_WORKERS, len(ranks))) as pool:
        futures = {pool.submit(probe_npu, rank, timeout): rank for rank in ranks}
        for future in as_completed(futures):
            rank = futures[future]
            try:
                results[rank] = future.result()
            except Exception as exc:  # 防御：单卡探测异常不应中断整轮检查
                results[rank] = ProbeResult(
                    rank, NPU_ALIAS_FORMAT.format(rank), False,
                    f"{type(exc).__name__}: {exc}")
    return [results[rank] for rank in sorted(results)]


def ensure_no_pass(args=None, ranks: Optional[List[int]] = None,
                   timeout: int = DEFAULT_PROBE_TIMEOUT,
                   interactive: bool = True) -> bool:
    """确保所有目标 NPU 免密可用。

    探测失败且处于交互终端时，询问是否调用 ``sprinkler -no-pass`` 重新配置；
    配置完成后再次探测。返回最终是否全部通过。
    """
    results = check_no_pass(ranks, timeout)
    failed = _print_report(results)
    if not failed:
        return True

    if not (interactive and sys.stdin.isatty()):
        print(f"{Colors.YELLOW}请先执行: sprinkler -no-pass{Colors.RESET}")
        return False

    print(f"{Colors.YELLOW}注意: sprinkler -no-pass 会重新生成密钥并重写 ~/.ssh/config。"
          f"{Colors.RESET}")
    try:
        answer = input("是否立即配置免密？[y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer not in ("y", "yes"):
        print(f"{Colors.YELLOW}请先执行: sprinkler -no-pass{Colors.RESET}")
        return False

    from sprinkler.auto.base.no_pass import no_pass_check
    no_pass_check(args)

    print("重新探测 NPU 免密...")
    failed = _print_report(check_no_pass(ranks, timeout))
    return not failed


def _print_report(results: List[ProbeResult]) -> List[ProbeResult]:
    """打印探测结果，返回失败列表。"""
    failed = [item for item in results if not item.ok]
    if not failed:
        print(f"{Colors.GREEN}NPU 免密检查通过: {len(results)} 张卡均可 "
              f"ssh root@npu<rank> 登录{Colors.RESET}")
    else:
        print(f"{Colors.RED}NPU 免密检查失败: {len(failed)}/{len(results)} 张卡不可免密登录"
              f"{Colors.RESET}")
        for item in failed:
            print(f"  {item.alias}: {item.reason}")
    return failed


def _last_line(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""
