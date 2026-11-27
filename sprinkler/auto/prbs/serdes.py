from __future__ import annotations

import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, List, Sequence

from sprinkler.connect.npu_access import npu_os_command

DEFAULT_UDIE = 0
DEFAULT_MACROS = tuple(range(8))
MAX_MACRO_INDEX = 16
DEFAULT_LANES = 4
DEFAULT_PARALLEL = 8
DEFAULT_TIMEOUT = 30
REMOTE_TOOL_PATH = "/root/serdestool.bin"

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
STATUS_RE = re.compile(
    r"serdes\[(?P<serdes>\d+)\]\s+macro\[(?P<macro>\d+)\]\s+"
    r"lane\[(?P<lane>\d+)\]\s+prbs:\s*en[=\s]+(?P<en>\d+).*?"
    r"\bber\s+(?P<ber>[0-9.eE+-]+)",
    re.IGNORECASE,
)


def prbs_set_command(tool_path: str, udie: int, macro: int,
                     direction: str, pattern: str, lanes: int) -> str:
    return (f"{tool_path} prbs -i {int(udie)} -s m{int(macro)}d0 "
            f"-d {direction} -v {pattern} -n {int(lanes)}")


def status_command(tool_path: str, udie: int, macro: int, lanes: int) -> str:
    return (f"{tool_path} prbs -c status -i {int(udie)} -s m{int(macro)}d0 "
            f"-t 1 -n {int(lanes)}")


def parse_status(text: str) -> List[dict]:
    """解析 ``prbs -c status`` 输出，返回每 lane 的 BER 与 en 状态。"""
    rows = []
    for match in STATUS_RE.finditer(_clean(text)):
        rows.append({
            "serdes": int(match.group("serdes")),
            "macro": int(match.group("macro")),
            "lane": int(match.group("lane")),
            "en": int(match.group("en")),
            "ber": float(match.group("ber")),
        })
    return rows


def check_status_ready(results: Sequence[dict],
                       lanes: int = DEFAULT_LANES) -> List[str]:
    """校验一轮 prbs status：命令成功且每个 macro 的 lanes 条 lane 均 en=1。"""
    problems = []
    for result in results:
        target = result.get("target") or f"NPU{result.get('rank', 0)}"
        if not result.get("ok"):
            reason = _last_line(result.get("stderr") or result.get("stdout")) \
                or f"rc={result.get('rc')}"
            problems.append(f"{target} 状态读取失败: {reason}")
            continue
        rows = parse_status(result.get("stdout", ""))
        if len(rows) != lanes:
            problems.append(f"{target} 状态读数不完整: 期望 {lanes} 条 lane，"
                            f"实际 {len(rows)} 条")
            continue
        for row in rows:
            if row["en"] != 1:
                problems.append(f"{target} serdes{row['serdes']} "
                                f"m{row['macro']} L{row['lane']}: en={row['en']}")
    return problems


def run_command(item: dict, command: str,
                timeout: int = DEFAULT_TIMEOUT) -> dict:
    """在单张 NPU 上执行一条 serdestool 命令。"""
    started = time.time()
    result = {
        "rank": int(item.get("rank", 0)),
        "target": item.get("target") or f"NPU{item.get('rank', 0)}",
        "command": command,
        "ok": False,
        "rc": 255,
        "stdout": "",
        "stderr": "",
        "elapsed": 0.0,
    }
    try:
        completed = subprocess.run(
            npu_os_command(item.get("execute_eid", ""), command), shell=True,
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        result.update(rc=124, stderr=f"命令超时（{timeout}s）",
                      elapsed=round(time.time() - started, 3))
        return result
    except Exception as exc:
        result.update(rc=125, stderr=str(exc),
                      elapsed=round(time.time() - started, 3))
        return result

    result.update(
        ok=completed.returncode == 0,
        rc=completed.returncode,
        stdout=_clean(completed.stdout or ""),
        stderr=_clean(completed.stderr or ""),
        elapsed=round(time.time() - started, 3),
    )
    return result


class NpuPrbs:
    """一批 NPU 上的 PRBS 开流/关流/读结果。"""

    def __init__(self, items: Sequence[dict], tool_path: str = REMOTE_TOOL_PATH,
                 udie: int = DEFAULT_UDIE, macros: Iterable[int] = DEFAULT_MACROS,
                 lanes: int = DEFAULT_LANES, parallel: int = DEFAULT_PARALLEL,
                 timeout: int = DEFAULT_TIMEOUT):
        self.items = list(items)
        self.tool_path = tool_path
        self.udie = int(udie)
        self.macros = [int(macro) for macro in macros]
        self.lanes = int(lanes)
        self.parallel = max(1, int(parallel))
        self.timeout = int(timeout)

    def _run(self, build: Callable[[dict], List[str]]) -> List[dict]:
        return run_per_npu(self.items, build, parallel=self.parallel,
                           timeout=self.timeout)

    def start(self) -> List[dict]:
        """开启所有 macro/lane 的 prbs31（tx+rx）。"""
        return self._run(lambda item: [
            prbs_set_command(self.tool_path, self.udie, macro, "txrx", "prbs31", self.lanes)
            for macro in self.macros])

    def stop(self) -> List[dict]:
        """关闭所有 macro/lane 的 prbs。"""
        return self._run(lambda item: [
            prbs_set_command(self.tool_path, self.udie, macro, "txrx", "prbs0", self.lanes)
            for macro in self.macros])

    def read_status(self) -> List[dict]:
        return self._run(lambda item: [
            status_command(self.tool_path, self.udie, macro, self.lanes)
            for macro in self.macros])


def run_per_npu(items: Sequence[dict], build_commands: Callable[[dict], List[str]],
                parallel: int = DEFAULT_PARALLEL,
                timeout: int = DEFAULT_TIMEOUT) -> List[dict]:
    """按 NPU 并行、NPU 内按命令串行执行，返回扁平的执行结果。"""
    items = list(items)
    if not items:
        return []

    def one(item):
        return [run_command(item, command, timeout=timeout)
                for command in build_commands(item)]

    workers = max(1, min(int(parallel), len(items)))
    if workers == 1:
        batches = [one(item) for item in items]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            batches = list(pool.map(one, items))
    return [result for batch in batches for result in batch]


def format_status_report(results: Sequence[dict]) -> List[str]:
    """把 prbs status 结果整理成每 (NPU, macro) 一行。"""
    lines = []
    grouped, missing = _group_parsed(results, parse_status)
    for (target, serdes, macro), rows in sorted(grouped.items()):
        rows.sort(key=lambda row: row["lane"])
        values = " ".join(f"L{row['lane']}={row['ber']:.6e}" for row in rows)
        lines.append(f"  {target} serdes{serdes} m{macro}: {values}")
    lines.extend(missing)
    return lines


def _group_parsed(results: Sequence[dict], parser: Callable[[str], List[dict]]):
    grouped = {}
    missing = []
    for result in results:
        rows = parser("\n".join([result.get("stdout", ""), result.get("stderr", "")]))
        if not rows:
            detail = _last_line(result.get("stderr") or result.get("stdout"))
            reason = detail or f"rc={result.get('rc')}"
            missing.append(f"  {result.get('target')}: 未解析到输出（{reason}）")
            continue
        for row in rows:
            key = (result.get("target"), row["serdes"], row["macro"])
            grouped.setdefault(key, []).append(row)
    return grouped, missing


def _clean(text: str) -> str:
    return ANSI_RE.sub("", str(text or "")).replace("\r", "")


def _last_line(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""
