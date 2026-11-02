"""
PTA分布式训练异常处理测试（单机模式）
单机=集群特例(1节点本地执行), 复用PtaClusterExecutor
"""

from sprinkler.base.log import RunLog
from sprinkler.connect.targets import ConnectionTarget
from sprinkler.logging import Colors, output_auto_details

from .pta_common import VALID_TEST_TYPES, build_pta_config, detect_npu_device_count, get_local_ip
from .pta_cluster.executor import PtaClusterExecutor
from .pta_cluster.models import PtaNodeInfo

def pta_test_check(args) -> int:
    log = RunLog(args, "pta_test")
    print(f"{Colors.BLUE}日志目录: {log.path}{Colors.RESET}")

    test_type = getattr(args, "op_type", "alltoall")
    if test_type not in VALID_TEST_TYPES:
        log.fail(f"不支持的测试类型: {test_type} (支持: {', '.join(VALID_TEST_TYPES)})")
        output_auto_details("FAIL", "pta_test", "无效测试类型")
        return 1
    nproc = int(getattr(args, "nproc_per_node", 0)) or None
    if nproc is None:
        nproc = detect_npu_device_count()
        if nproc == 0:
            log.fail("未检测到NPU设备")
            output_auto_details("FAIL", "pta_test", "无NPU设备")
            return 1
    master_addr = getattr(args, "master_addr", "localhost")
    if master_addr in ("localhost", "127.0.0.1"):
        local_ip = get_local_ip()
        if local_ip:
            master_addr = local_ip
    local_target = ConnectionTarget(
        name="local",
        host=master_addr or "127.0.0.1",
        port=22,
        user="root",
        password="",
    )
    node = PtaNodeInfo(target=local_target, node_rank=0, nproc=nproc, is_local=True)
    config = build_pta_config(args)
    config.nproc_per_node = nproc
    executor = PtaClusterExecutor(config, [node], log, args)
    return executor.execute()

