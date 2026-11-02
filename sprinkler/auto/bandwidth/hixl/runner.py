"""
HIXL benchmark runner.

Handles:
  - resolving the HIXL repository path (CLI override → config → interactive prompt)
  - checking that the required build artefacts exist
  - delegating to ``commands.build_comm_bench_cmd`` for argument translation
  - executing the wrapped script with captured output
  - parsing the resulting CSV files to display a clean bandwidth summary
"""

import csv
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from sprinkler.auto.bandwidth.hixl.commands import build_comm_bench_cmd
from sprinkler.base.log import RunLog
from sprinkler.config.config_manager import HixlConfigManager

CHECK_ID = "hixl_comm_bench"

# Relative paths inside the HIXL repository
_RUN_COMM_BENCH_REL = Path("benchmarks") / "comm_benchmark" / "scripts" / "run_comm_benchmark.py"
_BENCH_BIN_REL = Path("build") / "benchmarks" / "comm_benchmark" / "hixl_comm_bench"


def _resolve_hixl_path(hixl_path_override: str | None = None) -> str:
    """Return a valid HIXL repository root.

    Priority:
      1. ``-hixl-path`` CLI override
      2. ``HIXL_PATH`` from ``sprinkler_config/config.conf``
      3. Interactive prompt (with clone / build instructions)

    Raises:
        SystemExit: if the path is invalid or the user cancels.
    """
    if hixl_path_override:
        path = Path(hixl_path_override).expanduser().resolve()
        if not path.exists():
            print(f"[ERROR] HIXL 路径不存在: {path}")
            sys.exit(1)
        return str(path)

    manager = HixlConfigManager()
    return manager.get_hixl_path()


def _check_prerequisites(run_log: RunLog, hixl_root: str) -> tuple[str, str]:
    """Verify that the benchmark script and binary are present.

    Returns:
        ``(run_script_path, bench_bin_path)``.

    Raises:
        SystemExit: if either artefact is missing.
    """
    root = Path(hixl_root)

    run_script = root / _RUN_COMM_BENCH_REL
    bench_bin = root / _BENCH_BIN_REL

    if not run_script.exists():
        run_log.fail(f"run_comm_benchmark.py 未找到: {run_script}")
        run_log.fail("请确认已克隆 HIXL 仓库且路径正确。")
        run_log.fail("  git clone https://gitcode.com/cann/hixl.git")
        sys.exit(1)

    if not bench_bin.exists():
        run_log.fail(f"hixl_comm_bench 二进制未找到: {bench_bin}")
        run_log.fail("请确认已执行编译:")
        run_log.fail("  cd hixl && source /usr/local/Ascend/cann/set_env.sh && bash build.sh --examples")
        sys.exit(1)

    return str(run_script), str(bench_bin)


def _csv_dir(output_dir: str) -> Path:
    """Resolve the CSV output directory (handle relative paths)."""
    return Path(output_dir).resolve()


def _collect_csv_results(csv_dir: Path) -> list[dict]:
    """Read all comm_result_*.csv files and return their rows."""
    rows: list[dict] = []
    for csv_path in sorted(csv_dir.glob("comm_result_*.csv")):
        try:
            with csv_path.open(newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rows.append(row)
        except Exception as exc:
            # Log but don't fail on unreadable CSV
            print(f"  [WARN] 无法读取 {csv_path}: {exc}", file=sys.stderr)
    return rows


def _block_label(block_size_str: str) -> str:
    """Convert raw byte count to human-readable label (e.g. 16384 → 16K)."""
    try:
        bs = int(block_size_str)
    except (ValueError, TypeError):
        return block_size_str
    if bs % (1024 * 1024) == 0:
        return f"{bs // (1024 * 1024)}M"
    if bs % 1024 == 0:
        return f"{bs // 1024}K"
    return str(bs)


def _block_sort_key(block_size_str: str) -> int:
    """Sort key for block size labels."""
    try:
        return int(block_size_str)
    except (ValueError, TypeError):
        return 0


def _format_bandwidth_summary(rows: list[dict]) -> list[str]:
    """Format bandwidth results into a clean summary table with averages.

    Groups by transport × direction × block_size and computes the mean.
    Returns lines ready for ``run_log.info()``.
    """
    if not rows:
        return ["  (无带宽数据)"]

    # Group by transport + direction + block_size, collect bandwidth values
    groups: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for row in rows:
        try:
            bw = float(row.get("bandwidth_gbps", 0) or 0)
            if bw <= 0:
                continue
            key = (row.get("transport", "?"), row.get("direction", "?"), row.get("block_size", "0"))
            groups[key].append(bw)
        except (ValueError, TypeError):
            continue

    if not groups:
        return ["  (无有效带宽数据)"]

    # Regroup by transport+direction, then by block_size
    td_groups: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for (transport, direction, block_size), values in groups.items():
        td_groups[(transport, direction)][block_size].extend(values)

    lines: list[str] = []
    for (transport, direction), block_map in sorted(td_groups.items()):
        lines.append(f"  transport={transport}  direction={direction}")
        lines.append(f"  {'Block Size':>10}  {'Avg Bandwidth (GB/s)':>20}")
        lines.append(f"  {'-'*10}  {'-'*20}")
        for bs in sorted(block_map.keys(), key=_block_sort_key):
            values = block_map[bs]
            avg_bw = sum(values) / len(values)
            label = _block_label(bs)
            lines.append(f"  {label:>10}  {avg_bw:>20.3f}")
        lines.append("")

    return lines


def run_comm_bench_exec(args) -> int:
    """Execute ``run_comm_benchmark.py`` and display a clean bandwidth summary.

    This is the main entry point called from ``sprinkler/run.py``.
    """
    # Create RunLog instance for sprinkler-compatible output
    run_log = RunLog(args, default_check_id=CHECK_ID)
    run_log.info(f"log_dir={run_log.path}")

    # 1. Resolve HIXL repository path
    hixl_root = _resolve_hixl_path(getattr(args, "hixl_path", None))

    # 2. Check that the required build artefacts exist
    run_script, bench_bin = _check_prerequisites(run_log, hixl_root)

    # 3. Build the command line
    cmd = build_comm_bench_cmd(run_script, bench_bin, args)

    # 4. Print summary header and command
    desc = f"HIXL Comm Benchmark: transport={args.hixl_transport}"
    hixl_mode = getattr(args, "hixl_mode", None)
    if hixl_mode:
        desc += f" direction={hixl_mode}"
    rank_str = str(getattr(args, "rank", "") or "").strip()
    if rank_str and rank_str != "0-7":
        desc += f" rank={rank_str}"
    run_log.info(desc)
    run_log.info(f"  $ {' '.join(cmd)}")

    # 5. Execute — capture stdout, show stderr for errors
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stdout_data, stderr_data = process.communicate()
    rc = process.returncode

    # 6. If there's stderr output, show it (warnings/errors)
    if stderr_data and stderr_data.strip():
        for line in stderr_data.strip().splitlines():
            run_log.log("WARN", f"  (脚本 stderr) {line.strip()}")

    # 7. Parse CSV results and display bandwidth summary
    output_dir = _csv_dir(args.hixl_output_dir)
    csv_rows = _collect_csv_results(output_dir)

    if rc != 0 and not csv_rows:
        run_log.fail(f"测试失败 (exit code={rc})")
        # Show captured stdout for debugging
        if stdout_data and stdout_data.strip():
            run_log.info("--- 脚本输出 ---")
            for line in stdout_data.strip().splitlines():
                run_log.info(f"  {line}")
        run_log.finish_log_dir()
        return rc

    if csv_rows:
        run_log.info("")
        run_log.info("带宽结果:")
        for line in _format_bandwidth_summary(csv_rows):
            run_log.info(line)
    else:
        run_log.log("WARN", "未找到带宽数据 (CSV 结果为空)")

    # 8. Final result
    if rc == 0:
        run_log.ok("HIXL comm benchmark completed")
    else:
        run_log.fail(f"HIXL comm benchmark 部分失败 (exit code={rc})")

    run_log.finish_log_dir()
    return rc


def hixl_exec():
    """Dispatch HIXL benchmark commands (entry from ``sprinkler/run.py``)."""
    from sprinkler.arguments import get_args

    args = get_args()
    if getattr(args, 'hixl_bandwidth', False):
        rc = run_comm_bench_exec(args)
        if rc != 0:
            sys.exit(rc)
