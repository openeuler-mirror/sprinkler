"""主入口函数"""

import os
from pathlib import Path

from sprinkler.base.log import RunLog
from sprinkler.config.config_manager import MpirunConfigManager
from sprinkler.connect.targets import TargetContext
from sprinkler.logging import Colors
from sprinkler.auto.base.cluster_eth import get_interfaces_from_nodes

from .models import ClusterConfig
from .executor import SingleModeExecutor, ClusterModeExecutor, ParallelCabinetExecutor


def mpirun_cluster_check(args) -> int:
    """
    MPirun集群带宽测试主入口函数
    参数:
        args: 命令行参数对象
    返回:
        退出码 (0表示成功, 1表示失败)
    """
    log = RunLog(args, "mpirun_cluster")
    print(f"{Colors.BLUE}日志目录: {log.path}{Colors.RESET}")
    
    config_manager = MpirunConfigManager()
    cluster_conf = config_manager.get_cluster_conf()
    mpich_path = config_manager.get_mpich_path()
    cann_path = config_manager.get_cann_path()
    
    config = ClusterConfig(
        cluster_conf=cluster_conf,
        mpich_path=mpich_path,
        cann_path=cann_path,
        timeout_seconds=int(os.getenv("TIMEOUT_SECONDS", "180")),
    )
    
    try:
        if not hasattr(args, 'ca') or not args.ca:
            args.ca = getattr(args, 'cl', None)
        
        target_context = TargetContext(args)
        hosts = target_context.get_targets("cpu_os")
    except Exception as exc:
        log.fail(f"选择主机失败: {exc}")
        return 1
    
    if not hosts:
        log.fail("没有选择任何主机")
        return 1
    
    log.info("收集集群网卡配置...")
    interface_names = get_interfaces_from_nodes(args, log)
    if interface_names:
        log.info(f"网卡列表: {','.join(interface_names)}")
    else:
        log.info("未检测到网卡，使用默认配置")
    
    mpi_n = getattr(args, "mpi_n", 8)
    test_type = getattr(args, "test_type", "alltoall_test")
    hccl_test = getattr(args, "hccl_test", "aicpu_ts")
    hccl_buffsize = getattr(args, "hccl_buffer", 1024)
    log.info(f"HCCL_BUFFSIZE: {Colors.BLUE}{hccl_buffsize}{Colors.RESET}")
    single_mode = getattr(args, "single", False)
    parallel_cabinet = getattr(args, "parallel_cabinet", False)
    master_host = getattr(args, "master", None)
    
    if parallel_cabinet:
        cabinet_groups: dict[str, list] = {}
        for target in hosts:
            parts = target.name.split(":")
            cabinet_name = ":".join(parts[:2]) if len(parts) >= 2 else "default"
            if cabinet_name not in cabinet_groups:
                cabinet_groups[cabinet_name] = []
            cabinet_groups[cabinet_name].append(target)
        
        log.info(f"选择的主机 ({len(hosts)}台, {len(cabinet_groups)}个柜子):")
        for cabinet_name, cabinet_targets in cabinet_groups.items():
            primary = cabinet_targets[0]
            log.info(f"柜子 {cabinet_name} 主节点: {Colors.BLUE}{primary.name}{Colors.RESET} -> {primary.host}")
            if len(cabinet_targets) > 1:
                for target in cabinet_targets[1:]:
                    log.info(f"  从节点: {Colors.BLUE}{target.name}{Colors.RESET} -> {target.host}")
    elif single_mode:
        log.info(f"选择的主机 ({len(hosts)}台):")
        for target in hosts:
            log.info(f"位置：{Colors.BLUE}{target.name}{Colors.RESET} -> {target.host}")
    else:
        log.info(f"选择的主机 ({len(hosts)}台):")
        
        actual_master = master_host if master_host else hosts[0].host
        log.info(f"执行节点（主节点）：{Colors.BLUE}{actual_master}{Colors.RESET}")
        
        if hosts:
            log.info(f"参与节点列表：")
            for target in hosts:
                marker = " (执行节点)" if target.host == actual_master else ""
                log.info(f"位置：{Colors.BLUE}{target.name}{Colors.RESET} -> {target.host}{marker}")

    if single_mode:
        executor = SingleModeExecutor(config, hosts, log, args, hccl_test, interface_names, hccl_buffsize)
        success = executor.execute(mpi_n, test_type)
    elif parallel_cabinet:
        log_dir = Path(log.path)
        executor = ParallelCabinetExecutor(config, hosts, log, args, hccl_test, interface_names, hccl_buffsize)
        success = executor.execute(mpi_n, test_type, log_dir)
    else:
        log_dir = Path(log.path)
        executor = ClusterModeExecutor(config, hosts, log, args, hccl_test, master_host, interface_names, hccl_buffsize)
        success = executor.execute(mpi_n, test_type, log_dir)
    
    if success:
        log.info("MPI测试通过")
        return 0
    else:
        log.fail("MPI测试失败")
        return 1

