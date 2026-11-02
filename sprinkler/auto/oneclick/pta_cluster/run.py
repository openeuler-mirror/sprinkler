"""
PTA集群测试入口
从-sp/-ca/-node参数解析目标节点, 桥接TargetSelector与PtaClusterExecutor
"""

from sprinkler.base.log import RunLog
from sprinkler.connect.targets import TargetContext
from sprinkler.logging import Colors, output_auto_details

from ..pta_common import VALID_TEST_TYPES, build_pta_config, get_local_ips
from .executor import PtaClusterExecutor
from .models import PtaNodeInfo


def pta_cluster_check(args) -> int:
    log = RunLog(args, "pta_cluster")
    print(f"{Colors.BLUE}日志目录: {log.path}{Colors.RESET}")

    test_type = getattr(args, "op_type", "alltoall")
    if test_type not in VALID_TEST_TYPES:
        log.fail(f"不支持的测试类型: {test_type} (支持: {', '.join(VALID_TEST_TYPES)})")
        output_auto_details("FAIL", "pta_cluster", "无效测试类型")
        return 1

    nodes = _resolve_nodes(args, log)
    if not nodes:
        log.fail("未找到可用节点, 请检查-sp/-ca/-node参数")
        output_auto_details("FAIL", "pta_cluster", "无可用节点")
        return 1

    config = build_pta_config(args)
    executor = PtaClusterExecutor(config, nodes, log, args)
    return executor.execute()

def _resolve_nodes(args, log) -> list[PtaNodeInfo]:
    local_ips = set(get_local_ips())
    try:
        ctx = TargetContext(args)
        targets = ctx.get_targets("cpu_os")
    except Exception as exc:
        log.fail(f"节点解析失败: {exc}")
        return []

    nodes: list[PtaNodeInfo] = []
    for rank, target in enumerate(targets):
        is_local = target.host in local_ips
        nproc = int(getattr(args, "nproc_per_node", 0)) or 8
        nodes.append(PtaNodeInfo(
            target=target,
            node_rank=rank,
            nproc=nproc,
            is_local=is_local,
        ))
    return nodes
