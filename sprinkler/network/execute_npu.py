"""NPU 命令集执行 —— 合并原 read_dev / write_dev / execute_npu 三个入口。

寄存器读写不再单独占用参数，直接在命令集里写 devmem 调用即可：

    # 读寄存器
    devmem 0x1234
    # 写 32bit
    devmem 0x1234 32 0x1

命令集格式：UTF-8 文本，一行一条命令；空行和以 # 开头的行忽略。

执行流程：
    1. 取命令集路径（-execute-npu <path>；不带路径则运行时交互询问）
    2. 免密探测 npu0..npu7（ssh -o BatchMode=yes，不弹密码）
       - 全部不可达 -> 退出并提示先执行 -no-pass
       - 部分不可达 -> 打印可达/不可达清单，只在可达 NPU 上执行
    3. 各可达 NPU 并行，NPU 内部按行串行执行命令集
    4. 逐 NPU 汇总每一条命令的结果，完整输出落盘到 sprinkler_logs/execute_npu
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from sprinkler.arguments import get_args
from sprinkler.logging import Colors
from sprinkler.utils.utils import default_log_root, read_text_command_file

# no_pass 模块在 ~/.ssh/config 里为每个设备写 Host npu<dev_id>，这里按默认 8 卡探测。
NPU_ALIAS_FORMAT = "npu{}"
DEFAULT_NPU_COUNT = 8

# 探测和下发都用仅密钥认证：BatchMode 禁止任何交互式提问，避免卡在密码输入。
SSH_OPTIONS = (
    "-o", "BatchMode=yes",
    "-o", "PasswordAuthentication=no",
    "-o", "ConnectTimeout=5",
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
)

PROBE_TIMEOUT = 8
DEFAULT_COMMAND_TIMEOUT = 30
OUTPUT_PREVIEW_LINES = 6
TIMEOUT_ENV = "SPRINKLER_EXECUTE_NPU_TIMEOUT"

EXIT_OK = 0
EXIT_COMMAND_FAILED = 1
EXIT_CANNOT_RUN = 2


@dataclass
class CommandResult:
    """单条命令在单台 NPU 上的执行结果。"""

    index: int
    lineno: int
    command: str
    rc: int
    stdout: str = ""
    stderr: str = ""
    elapsed: float = 0.0
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.rc == 0 and not self.timed_out


@dataclass
class NpuExecution:
    """一台 NPU 上整份命令集的执行结果。"""

    alias: str
    results: List[CommandResult] = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def ok_count(self) -> int:
        return sum(1 for item in self.results if item.ok)

    @property
    def fail_count(self) -> int:
        return sum(1 for item in self.results if not item.ok)


def _use_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _c(text: str, color: str) -> str:
    return "{}{}{}".format(color, text, Colors.RESET) if _use_color() else text


def _display_width(text: str) -> int:
    """终端显示宽度：中文/全角按 2 列算，避免表格错位。"""
    width = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in {"W", "F"} else 1
    return width


def _pad(text: str, width: int, align: str = "left") -> str:
    """按显示宽度补齐到 width 列（文本需为纯文本，不含颜色转义）。"""
    gap = max(0, width - _display_width(text))
    return (" " * gap + text) if align == "right" else (text + " " * gap)


def _pad_colored(text: str, width: int, color: Optional[str], align: str = "right") -> str:
    """先按纯文本宽度补位，再染色，避免转义码干扰列宽。"""
    padded = _pad(text, width, align)
    return _c(padded, color) if color else padded


def _alias_index(alias: str) -> int:
    digits = "".join(ch for ch in alias if ch.isdigit())
    return int(digits) if digits else 0


def _npu_aliases() -> List[str]:
    """候选 NPU 别名：默认 npu0..npu7，若能解析到设备对则并入其 dev_id。"""
    ids = set(range(DEFAULT_NPU_COUNT))
    try:
        from sprinkler.config.host_device_pairs import get_host_and_device_pairs

        for key in get_host_and_device_pairs():
            try:
                dev_id = int(key)
            except (TypeError, ValueError):
                continue
            if dev_id >= 0:
                ids.add(dev_id)
    except Exception:
        # 取不到真实设备表时按默认 8 卡探测，不让探测阶段因此失败。
        pass
    return [NPU_ALIAS_FORMAT.format(index) for index in sorted(ids)]


def _command_timeout() -> int:
    raw = os.environ.get(TIMEOUT_ENV, "")
    if raw.strip().isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_COMMAND_TIMEOUT


def _ssh_run(alias: str, remote_command: str, timeout: int) -> Tuple[int, str, str, bool]:
    """在指定别名上执行一条远端命令，返回 (rc, stdout, stderr, 是否超时)。"""
    try:
        proc = subprocess.run(
            ["ssh", *SSH_OPTIONS, alias, remote_command],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, "", "命令超时（{}s）".format(timeout), True
    except OSError as exc:
        return 127, "", str(exc), False
    return proc.returncode, proc.stdout or "", proc.stderr or "", False


def _probe_npu(alias: str) -> Tuple[str, bool, str]:
    """免密探测：能 ssh 上去执行 true 就算可达。"""
    rc, _, stderr, timed_out = _ssh_run(alias, "true", PROBE_TIMEOUT)
    if rc == 0 and not timed_out:
        return alias, True, ""

    lines = [line.strip() for line in stderr.strip().splitlines() if line.strip()]
    detail = lines[-1] if lines else "ssh 返回码 {}".format(rc)
    if "Could not resolve hostname" in detail:
        detail = "{}（~/.ssh/config 中没有该别名，需先执行 -no-pass）".format(detail)
    return alias, False, detail


def _read_commands(path: Path) -> List[Tuple[int, str]]:
    """读命令集，返回 [(原始行号, 命令)]，跳过空行与 # 注释。"""
    commands: List[Tuple[int, str]] = []
    text = read_text_command_file(path)
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        commands.append((lineno, line))
    return commands


def _ask_command_file() -> Optional[Path]:
    """交互询问命令集路径，返回 None 表示用户取消。"""
    if not sys.stdin.isatty():
        print(_c("[FAIL] 非交互环境无法询问，请用 -execute-npu <命令集路径> 指定文件", Colors.RED))
        return None

    print("未通过 -execute-npu 指定命令集路径，请现在输入。")
    while True:
        try:
            raw = input("命令集文件路径（输入 q 取消）: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not raw:
            continue
        if raw.lower() in {"q", "quit", "exit"}:
            return None
        path = Path(raw).expanduser()
        if path.is_file():
            return path
        print(_c("文件不存在或不是普通文件: {}".format(path), Colors.YELLOW))


def _resolve_command_file(raw: Optional[str]) -> Optional[Path]:
    text = (raw or "").strip()
    # 兼容历史上 default="None" 的字符串默认值，避免把 "None" 当成路径。
    if text.lower() in {"", "none"}:
        text = ""
    if not text:
        return _ask_command_file()
    path = Path(text).expanduser()
    if not path.is_file():
        print(_c("[FAIL] 命令集文件不存在: {}".format(path), Colors.RED))
        return None
    return path


def _run_on_npu(alias: str, commands: Sequence[Tuple[int, str]], timeout: int) -> NpuExecution:
    """在一台 NPU 上按行串行执行命令集。"""
    started = time.monotonic()
    execution = NpuExecution(alias=alias)
    for index, (lineno, command) in enumerate(commands, 1):
        step_started = time.monotonic()
        # 远端用登录 shell 执行，和人工 ssh 上去敲命令的环境一致；命令整体做引号保护。
        remote_command = "bash -lc {}".format(shlex.quote(command))
        rc, stdout, stderr, timed_out = _ssh_run(alias, remote_command, timeout)
        execution.results.append(
            CommandResult(
                index=index,
                lineno=lineno,
                command=command,
                rc=rc,
                stdout=stdout,
                stderr=stderr,
                elapsed=time.monotonic() - step_started,
                timed_out=timed_out,
            )
        )
    execution.elapsed = time.monotonic() - started
    return execution


def _preview(text: str, limit: int = OUTPUT_PREVIEW_LINES) -> List[str]:
    """截取输出前若干非空行，避免一台 NPU 的日志刷屏。"""
    lines = [line.rstrip() for line in (text or "").strip().splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return []
    if len(lines) <= limit:
        return lines
    return lines[:limit] + ["...（另有 {} 行，完整输出见日志）".format(len(lines) - limit)]


def _write_logs(log_dir: Path, command_file: Path, executions: Sequence[NpuExecution]) -> None:
    """每台 NPU 一份完整日志 + 一份总览。"""
    log_dir.mkdir(parents=True, exist_ok=True)

    for execution in executions:
        lines = [
            "NPU: {}".format(execution.alias),
            "命令集: {}".format(command_file),
            "总耗时: {:.2f}s".format(execution.elapsed),
            "",
        ]
        for item in execution.results:
            lines.append("=" * 70)
            lines.append("[{}] 第 {} 行: {}".format(item.index, item.lineno, item.command))
            lines.append("rc={} 耗时={:.2f}s 超时={}".format(item.rc, item.elapsed, item.timed_out))
            lines.append("--- stdout ---")
            lines.append(item.stdout.rstrip())
            lines.append("--- stderr ---")
            lines.append(item.stderr.rstrip())
        (log_dir / "{}.log".format(execution.alias)).write_text(
            "\n".join(lines) + "\n", encoding="utf-8")

    total_commands = sum(len(item.results) for item in executions)
    total_failed = sum(item.fail_count for item in executions)
    summary = [
        "命令集: {}".format(command_file),
        "执行 NPU: {} 台".format(len(executions)),
        "命令条数: {}".format(max((len(item.results) for item in executions), default=0)),
        "执行总次数: {}，成功 {}，失败 {}".format(
            total_commands, total_commands - total_failed, total_failed
        ),
        "",
    ]
    for execution in executions:
        summary.append(
            "{:<8} 成功 {}/{}  耗时 {:.2f}s".format(
                execution.alias, execution.ok_count, len(execution.results), execution.elapsed
            )
        )
    (log_dir / "summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")


def _print_probe_result(reachable: List[str], unreachable: List[Tuple[str, str]], total: int) -> None:
    print("[1/3] 免密探测（ssh -o BatchMode=yes npu0..npu{})".format(total - 1))
    if reachable:
        print("  {} 可达 {} 台: {}".format(
            _c("✔", Colors.GREEN), len(reachable), " ".join(reachable)))
    for alias, detail in unreachable:
        print("  {} 不可达 {}: {}".format(_c("✘", Colors.RED), alias, detail))
    print()


def _print_summary(executions: Sequence[NpuExecution], log_dir: Path, command_file: Path) -> None:
    print()
    print("=" * 78)
    print(_c("执行汇总", Colors.BOLD))
    print("=" * 78)
    print("  命令集: {}".format(command_file))
    col_npu, col_cmd, col_ok, col_fail, col_time = 8, 6, 6, 6, 9
    header = "  {} {} {} {} {}".format(
        _pad("NPU", col_npu),
        _pad("命令", col_cmd, "right"),
        _pad("成功", col_ok, "right"),
        _pad("失败", col_fail, "right"),
        _pad("耗时", col_time, "right"),
    )
    print(header)
    for execution in executions:
        failed = execution.fail_count
        print("  {} {} {} {} {}".format(
            _pad(execution.alias, col_npu),
            _pad(str(len(execution.results)), col_cmd, "right"),
            _pad(str(execution.ok_count), col_ok, "right"),
            _pad_colored(str(failed), col_fail, Colors.RED if failed else None),
            _pad("{:.2f}s".format(execution.elapsed), col_time, "right"),
        ))

    total_commands = sum(len(item.results) for item in executions)
    total_failed = sum(item.fail_count for item in executions)
    print("  合计: {} 台 NPU / {} 次执行 / 成功 {} / 失败 {}".format(
        len(executions), total_commands, total_commands - total_failed,
        _c(str(total_failed), Colors.RED) if total_failed else "0"))

    print()
    print(_c("逐 NPU 明细", Colors.BOLD))
    for execution in executions:
        head = "── {} {} {}/{} ──".format(
            execution.alias,
            _c("✔", Colors.GREEN) if execution.fail_count == 0 else _c("✘", Colors.RED),
            execution.ok_count,
            len(execution.results),
        )
        print(head)
        for item in execution.results:
            if item.ok:
                print("  [{}] {} {:.2f}s  {}".format(
                    item.index, _c("✔", Colors.GREEN), item.elapsed, item.command))
                for line in _preview(item.stdout):
                    print("        {}".format(line))
            else:
                reason = "超时" if item.timed_out else "rc={}".format(item.rc)
                print("  [{}] {} {:.2f}s  {}  ({})".format(
                    item.index, _c("✘", Colors.RED), item.elapsed, item.command, reason))
                for line in _preview(item.stderr):
                    print("        {}".format(line))
    print()
    print("完整日志: {}".format(log_dir))


def execute_npu(args=None) -> int:
    """-execute-npu 入口。返回 0 全成功 / 1 有命令失败 / 2 无法执行。"""
    args = args if args is not None else get_args()
    raw = getattr(args, "execute_npu", None)
    if raw is None:
        # 参数未出现在命令行，直接调用本函数时不做任何事。
        return EXIT_OK

    command_file = _resolve_command_file(raw)
    if command_file is None:
        return EXIT_CANNOT_RUN

    commands = _read_commands(command_file)
    if not commands:
        print(_c("[FAIL] 命令集为空或只有注释: {}".format(command_file), Colors.RED))
        return EXIT_CANNOT_RUN

    aliases = _npu_aliases()
    print("=" * 78)
    print(_c("NPU 命令集执行", Colors.BOLD))
    print("=" * 78)
    print("  命令集: {} （{} 条命令）".format(command_file, len(commands)))
    print("  目标 NPU: {} 台 ({})".format(len(aliases), " ".join(aliases)))
    print()

    with ThreadPoolExecutor(max_workers=min(len(aliases), 16)) as pool:
        probes = list(pool.map(_probe_npu, aliases))

    reachable = [alias for alias, ok, _ in probes if ok]
    unreachable = [(alias, detail) for alias, ok, detail in probes if not ok]
    _print_probe_result(reachable, unreachable, len(aliases))

    if not reachable:
        print(_c("[FAIL] {} 台 NPU 全部无法免密登录，未执行任何命令。".format(len(aliases)), Colors.RED))
        print("       请先执行: {}".format(_c("sprinkler -no-pass", Colors.YELLOW)))
        return EXIT_CANNOT_RUN

    if unreachable:
        print("  {} 仅在 {} 台可达 NPU 上执行。".format(
            _c("部分不可达:", Colors.YELLOW), len(reachable)))
        print()

    timeout = _command_timeout()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = default_log_root() / "execute_npu" / stamp

    print("[2/3] 并行执行（每台 NPU 按行执行，单条超时 {}s）".format(timeout))
    executions: List[NpuExecution] = []
    with ThreadPoolExecutor(max_workers=len(reachable)) as pool:
        futures = {
            pool.submit(_run_on_npu, alias, commands, timeout): alias
            for alias in reachable
        }
        for future in as_completed(futures):
            executions.append(future.result())
    executions.sort(key=lambda item: _alias_index(item.alias))
    print("  完成。")

    _write_logs(log_dir, command_file, executions)
    print("[3/3] 汇总")
    _print_summary(executions, log_dir, command_file)

    return EXIT_OK if all(item.fail_count == 0 for item in executions) else EXIT_COMMAND_FAILED
#（注：内容由AI生成）
