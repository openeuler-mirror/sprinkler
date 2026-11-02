"""
HIXL benchmark command construction.

Builds the argument list for calling HIXL's ``run_comm_benchmark.py``
from sprinkler CLI arguments.
"""

import sys

from sprinkler.base.ranges import parse_range
from sprinkler.utils.utils import argv_has_flag


def _normalize_pattern(pattern: str) -> str:
    """Normalize pattern strings: ``one-to-many`` → ``one_to_many``."""
    return pattern.replace("-", "_")


def _resolve_device_ids(args) -> str | None:
    """Resolve device IDs from ``-rank`` argument.

    Supports both ``-rank 4,5`` and ``-rank 0-7`` formats.
    Returns a comma-separated string like ``"4,5"``, or ``None`` if
    the user did not explicitly pass ``-rank`` (let the script auto-detect).
    """
    if not argv_has_flag("-rank"):
        return None
    rank = str(getattr(args, "rank", "") or "").strip()
    if not rank:
        return None
    try:
        ids = parse_range(rank)
        if not ids:
            return None
        return ",".join(str(i) for i in ids)
    except ValueError:
        return rank


def build_comm_bench_cmd(
    run_script: str,
    bench_bin: str,
    args,
) -> list[str]:
    """Build the run_comm_benchmark.py command line from sprinkler args.

    Args:
        run_script: Full path to ``run_comm_benchmark.py``.
        bench_bin: Full path to the ``hixl_comm_bench`` binary.
        args: Parsed sprinkler command-line namespace.

    Returns:
        Complete ``[executable, script, ...]`` command list.
    """
    cmd = [sys.executable, run_script]

    # --- topology ---
    pattern = _normalize_pattern(getattr(args, "hixl_pattern", "pairwise"))
    if pattern != "pairwise":
        cmd.append(f"--pattern={pattern}")
    if args.hixl_num_targets is not None:
        cmd.append(f"--num_targets={args.hixl_num_targets}")
    if args.hixl_num_initiators is not None:
        cmd.append(f"--num_initiators={args.hixl_num_initiators}")

    # bench_bin explicit (skip script's auto-detect)
    cmd.append(f"--bench_bin={bench_bin}")

    if args.hixl_host:
        cmd.append(f"--host={args.hixl_host}")
    if args.hixl_role:
        cmd.append(f"--role={args.hixl_role}")
    if args.hixl_target_host:
        cmd.append(f"--target-host={args.hixl_target_host}")
    if args.hixl_base_port != 16000:
        cmd.append(f"--base_hixl_port={args.hixl_base_port}")

    # Device IDs from -rank (reuse existing sprinkler parameter)
    device_ids = _resolve_device_ids(args)
    if device_ids:
        cmd.append(f"--device_ids={device_ids}")

    # --- transport ---
    if args.hixl_transport:
        cmd.append(f"--transport={args.hixl_transport}")

    # Direction from -hixl-mode
    hixl_mode = getattr(args, "hixl_mode", None)
    if hixl_mode:
        cmd.append(f"--direction={hixl_mode}")
    if args.hixl_host_roce_ip:
        cmd.append(f"--host_roce_ip={args.hixl_host_roce_ip}")

    # --- data sizes ---
    if args.hixl_block_sizes:
        cmd.append(f"--block_sizes={args.hixl_block_sizes}")
    if args.hixl_transfer_size:
        cmd.append(f"--transfer_size={args.hixl_transfer_size}")
    if args.hixl_buffer_size:
        cmd.append(f"--buffer_size={args.hixl_buffer_size}")
    if args.hixl_loops != 5:
        cmd.append(f"--loops={args.hixl_loops}")
    if args.hixl_output_dir:
        cmd.append(f"--output_dir={args.hixl_output_dir}")

    # --- HIXL init options ---
    if args.hixl_option:
        for opt in args.hixl_option:
            cmd.append(f"-H={opt}")

    # --- report ---
    if args.hixl_skip_plot:
        cmd.append("--skip_plot")
    if args.hixl_skip_report:
        cmd.append("--skip_report")

    # --- runtime ---
    if args.hixl_peer_wait_s is not None:
        cmd.append(f"--peer_wait_s={args.hixl_peer_wait_s}")
    if args.hixl_inter_run_delay_s is not None:
        cmd.append(f"--inter_run_delay_s={args.hixl_inter_run_delay_s}")
    if args.hixl_connect_timeout_ms is not None:
        cmd.append(f"--connect_timeout_ms={args.hixl_connect_timeout_ms}")

    return cmd
