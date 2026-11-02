"""
MPirun单机带宽测试功能
单机=集群特例(1节点本地执行), 复用ClusterModeExecutor
"""

import os
from pathlib import Path

from sprinkler.base.log import RunLog
from sprinkler.config.config_manager import MpirunConfigManager
from sprinkler.connect.targets import ConnectionTarget
from sprinkler.logging import Colors

from .mpirun_cluster.models import ClusterConfig
from .mpirun_cluster.executor import ClusterModeExecutor
from .pta_common import get_local_ip


def mpirun_bandwidth_check(args) -> int:
    """
    MPirun单机带宽测试主入口函数
    单机模式: 自动检测本机IP, 作为集群单节点特例执行
    参数:
        args: 命令行参数对象
    返回:
        退出码 (0表示成功, 1表示失败)
    """
    log = RunLog(args, "mpirun_bandwidth")
    print(f"{Colors.BLUE}日志目录: {log.path}{Colors.RESET}")

    config_manager = MpirunConfigManager()
    mpich_path = config_manager.get_mpich_path()
    cann_path = config_manager.get_cann_path()

    config = ClusterConfig(
        cluster_conf=config_manager.get_cluster_conf(),
        mpich_path=mpich_path,
        cann_path=cann_path,
        timeout_seconds=int(os.getenv("TIMEOUT_SECONDS", "45")),
    )

    local_ip = get_local_ip() or "127.0.0.1"
    local_target = ConnectionTarget(
        name="local",
        host=local_ip,
        port=22,
        user="root",
        password="",
    )

    mpi_n = getattr(args, "mpi_n", 8)
    test_type = getattr(args, "test_type", "alltoall_test")
    hccl_test = getattr(args, "hccl_test", "aicpu_ts")
    hccl_buffsize = getattr(args, "hccl_buffer", 1024)

    log.info(f"HCCL_BUFFSIZE: {Colors.BLUE}{hccl_buffsize}{Colors.RESET}")

    executor = ClusterModeExecutor(
        config, [local_target], log, args,
        hccl_test=hccl_test,
        interface_names=[],
        hccl_buffsize=hccl_buffsize,
    )
    success = executor.execute(mpi_n, test_type, Path(log.path))

    if success:
        log.info("MPI PASS")
        return 0
    else:
        log.fail("MPI FAIL")
        return 1
