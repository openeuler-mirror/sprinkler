"""UBOE bandwidth orchestration for `-urma-bandwidth -mode uboe`.

Mirrors the orchestration role of `urma/planner.py` + `core/runner.py`, driving UBOE
through the SAME generic execution pipeline: planning -> device-side env pre-check ->
filtered traffic, reusing `urma.planner.UrmaTrafficRunner` with the UBOE command builder
and show resolver injected (so process execution, bandwidth parsing and reports are all
reused unchanged).

Bond-ip reachability is NOT pre-pinged from the executing host -- UBOE traffic is launched
from each card's device side, so an external ping would only add a false gate. Commands
are printed by `UrmaTrafficRunner.run()` before traffic (mirrors URMA bandwidth).
"""

from sprinkler.auto.bandwidth.common import tag_results
from sprinkler.auto.bandwidth.uboe.commands import UboeCommandBuilder
from sprinkler.auto.bandwidth.uboe.show_resolver import UboeShowResolver
from sprinkler.auto.bandwidth.uboe.pairs import plan_uboe_pairs
from sprinkler.auto.bandwidth.urma.executor import UrmaCommandExecutor
from sprinkler.auto.bandwidth.urma.planner import UrmaTrafficRunner

UBOE_TRAFFIC_FIELDS = ("client_udma", "server_udma", "client_bond_ip", "server_bond_ip")


def _run_traffic(args, run_log, pairs, remote_executor=None):
    from sprinkler.auto.bandwidth.uboe import env as uboe_env
    ready_pairs, env_failed = uboe_env.precheck_pairs_for_traffic(args, pairs, run_log=run_log,
                                                                  remote_executor=remote_executor)
    for item in env_failed:
        run_log.log("WARN", f"env_failed pair={item.get('target')} reason={item.get('reason')}")

    # UrmaTrafficRunner.run() prints the exact listener/client commands before launching
    # (mirrors URMA bandwidth), so we only need to pass runnable pairs in.
    traffic_pairs, skipped = [], 0
    for pair in ready_pairs:
        missing = [name for name in UBOE_TRAFFIC_FIELDS if pair.get(name) in ("", None)]
        if missing:
            skipped += 1
            run_log.log("WARN", f"uboe pair not executable: pair={pair.get('target')} "
                                f"missing={','.join(missing)}")
        else:
            traffic_pairs.append(pair)
    if skipped:
        run_log.info(f"uboe_skipped_not_executable={skipped}")

    executor = UrmaCommandExecutor(args)
    traffic_results = UrmaTrafficRunner(
        args=args, run_log=run_log,
        command_builder=UboeCommandBuilder(args),
        show_resolver=UboeShowResolver(executor),
    ).run(traffic_pairs) if traffic_pairs else []
    results = list(env_failed)
    results += tag_results(traffic_results, bandwidth_phase="uboe", ping=True)
    return results


def run_uboe_bandwidth(args, run_log):
    ranks, pairs = plan_uboe_pairs(args)
    run_log.info(f"rank={','.join(str(rank) for rank in ranks)} pair_total={len(pairs)}")
    return _run_traffic(args, run_log, pairs)


def build_remote_executor(args):
    """Paramiko access to the peer cpu host(s) used by cross-board env pre-check."""
    try:
        from sprinkler.connect.base import ResultProcessor
        from sprinkler.connect.ssh import SSHOSConnector
        from sprinkler.auto.bandwidth.uboe.pairs import peer_cpu_targets
        from sprinkler.auto.bandwidth.uboe.remote import RemoteDeviceExecutor
        targets = peer_cpu_targets(args)
        if not targets:
            return None
        manager = SSHOSConnector(args=args, result_processor=ResultProcessor())
        return RemoteDeviceExecutor(manager, targets)
    except Exception:
        return None


def run_uboe_xboard_bandwidth(args, run_log):
    from sprinkler.auto.bandwidth.uboe.pairs import plan_uboe_xboard_pairs
    ranks, pairs = plan_uboe_xboard_pairs(args)
    run_log.info(f"phase=uboe-xboard rank={','.join(str(rank) for rank in ranks)} pair_total={len(pairs)}")
    remote_executor = build_remote_executor(args)
    try:
        return _run_traffic(args, run_log, pairs, remote_executor=remote_executor)
    finally:
        if remote_executor is not None:
            remote_executor.close()
