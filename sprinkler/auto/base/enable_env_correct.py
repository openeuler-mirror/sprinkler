"""集群环境检查模块：编排高性能模式、分bin、功率三项检查"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from sprinkler.auto.base.check_result import CheckResult, NodeCheckResult
from sprinkler.auto.base.check_hp_mode import check_local as hp_check_local
from sprinkler.auto.base.check_hp_mode import check_remote as hp_check_remote
from sprinkler.auto.base.check_board_id import check_local as board_check_local
from sprinkler.auto.base.check_board_id import check_remote as board_check_remote
from sprinkler.auto.base.check_power import check_local as power_check_local
from sprinkler.auto.base.check_power import check_remote as power_check_remote
from sprinkler.base.log import RunLog
from sprinkler.connect.connection_runner import create_runner
from sprinkler.connect.targets import TargetContext


def check_local_env(device_count: int = 8) -> list[CheckResult]:
    """本机环境检查（高性能模式 + 分bin）"""
    return [
        hp_check_local(device_count=device_count),
        board_check_local(device_count=device_count),
    ]


def check_local_power(test_rate_file: str, device_count: int = 8) -> CheckResult:
    """本机功率检查"""
    return power_check_local(test_rate_file, device_count=device_count)


def _check_node(runner, target, device_count: int) -> NodeCheckResult:
    """单节点环境检查（供线程池调用）"""
    node_name = getattr(target, "host", str(target))
    node_result = NodeCheckResult(node=node_name)
    node_result.checks.append(hp_check_remote(runner, target, device_count=device_count))
    node_result.checks.append(board_check_remote(runner, target, device_count=device_count))
    return node_result


def cluster_env_check(args, check_power: bool = False):
    """集群范围环境检查入口函数（异步并行）"""
    run_log = RunLog(args, "CLUSTER-env-check")
    run_log.info("开始集群环境检查")

    targets = TargetContext(args).get_targets("cpu_os")
    runner = create_runner(args, connection_type="ssh_os")
    device_count = 8

    all_results: dict[str, NodeCheckResult] = {}
    failed_nodes: list[str] = []

    with ThreadPoolExecutor(max_workers=min(len(targets), 16)) as pool:
        future_to_name = {}
        for target in targets:
            node_name = getattr(target, "host", str(target))
            future = pool.submit(_check_node, runner, target, device_count)
            future_to_name[future] = node_name

        for future in as_completed(future_to_name):
            node_name = future_to_name[future]
            try:
                node_result = future.result()
            except Exception as exc:
                run_log.fail_error("cpu_os", exc)
                failed_nodes.append(node_name)
                continue

            all_results[node_name] = node_result

    for target in targets:
        node_name = getattr(target, "host", str(target))
        if node_name not in all_results:
            continue
        node_result = all_results[node_name]

        node_level = "PASS" if node_result.all_passed else "FAIL"
        check_summary = ", ".join(
            f"{'✓' if c.status else '✗'}{c.name}" for c in node_result.checks
        )
        run_log.log(node_level, f"{node_name}: [{check_summary}]")

        for chk in node_result.checks:
            if chk.status:
                run_log.ok(f"  {chk.name}: {chk.message}")
            else:
                run_log.fail(f"  {chk.name}: {chk.message}")

    total = len(all_results)
    passed = sum(1 for r in all_results.values() if r.all_passed)
    run_log.info(f"集群环境检查汇总: {passed}/{total} 节点全部通过")
    if failed_nodes:
        run_log.fail(f"连接失败节点: {', '.join(failed_nodes)}")

    return list(all_results.values())
