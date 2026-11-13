import os
import sys
import subprocess
import datetime

from sprinkler.arguments import get_args
from sprinkler.utils.utils import _RUN_PATH, find_all_shell, package_path
from sprinkler.auto.bandwidth.svm_bandwidth import svm_bandwidth


def run_from_args(args):
    roads = {}
    if args.auto or args.all:
        command = ['bash', str(package_path() / "auto" / "auto.sh")]
        if args.auto:
            command.extend(['--level', args.auto])
        process = subprocess.Popen(command,
                           stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT,
                           text=True,
                           encoding='utf-8',
                           errors='replace',
                           bufsize=1)
        for line in process.stdout:
            print(line, end='')
        process.wait()


def stop_urma(args):
    roads = str(package_path() / "network" / "stop_npu_urma.sh")

    command = ['bash', roads]
    process = subprocess.Popen(command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding='utf-8',
                        errors='replace',
                        bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()
    

def device_online(args):
    """设备上线状态检查 - Python实现"""
    from sprinkler.auto.health.device_online import device_online_check
    return device_online_check(args)

def stop_firewall(args):
    """防火墙检测与关闭（检测到未关闭时交互式询问是否关闭）"""
    from sprinkler.auto.health.stop_firewall import stop_firewall_check
    return stop_firewall_check(args)

def config_hugepage(args):
    """配置CPU大页内存 - Python实现"""
    from sprinkler.auto.health.config_hugepage import config_hugepage_check
    return config_hugepage_check(args)

def check_eth():
    roads = str(package_path() / "auto" / "health" / "check_eth.sh")

    command = ['bash', roads]
    process = subprocess.Popen(command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding='utf-8',
                        errors='replace',
                        bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()
    
def npu_set_device(args):
    """NPU设备设置 - Python实现"""
    from sprinkler.auto.health.npu_set_device import npu_set_device_check
    return npu_set_device_check(args)

def npu_check_resource(args):
    """NPU资源状态检查 - Python实现"""
    from sprinkler.auto.health.npu_resource_check import npu_resource_check
    return npu_resource_check(args)

def npu_kill(args):
    """NPU资源释放 - Python实现"""
    from sprinkler.auto.health.npu_resource_kill import npu_resource_kill
    return npu_resource_kill(args)

def enable_env(args):
    """环境使能检查 - 复用 -env-check 的 Python 实现（高性能模式 + 分bin）"""
    return env_check(args)

def single_operator(args):
    """单算子测试 - 原 single_operator.sh 已不存在，功能待补"""
    print("ERROR: -single-operator 尚未提供 Python 实现（原 sprinkler/env/auto/single_operator.sh 已不存在）")
    print("       建议复用 oneclick/pta_common.py 的 TEST_SCRIPT_MAP 机制补写")
    return 1
    
def mpirun_bandwidth(args):
    """MPirun单机带宽测试 - Python实现"""
    from sprinkler.auto.oneclick.mpirun_bandwidth import mpirun_bandwidth_check
    return mpirun_bandwidth_check(args)

def cluster_mpi(args):
    """Cluster MPI bandwidth test - Python实现"""
    from sprinkler.auto.oneclick.mpirun_cluster import mpirun_cluster_check
    return mpirun_cluster_check(args)


def pta_test(args):
    """PTA分布式训练异常处理测试(单机) - Python实现"""
    from sprinkler.auto.oneclick.pta_test import pta_test_check
    return pta_test_check(args)


def pta_cluster(args):
    """PTA集群测试 - Python实现, 使用-sp/-ca/-node指定节点"""
    from sprinkler.auto.oneclick.pta_cluster.run import pta_cluster_check
    return pta_cluster_check(args)


def get_power(args):
    """额定功率检查 - Python 实现，需配合 -test-rate-file 指定工作目录"""
    from sprinkler.auto.base.check_power import check_local as power_check_local

    test_rate_file = getattr(args, "test_rate_file", None)
    if not test_rate_file:
        print("ERROR: -get-power 需配合 -test-rate-file 指定 test/lib/host 所在目录")
        return 1
    result = power_check_local(test_rate_file, device_count=getattr(args, "device_count", 8))
    print(f"{'PASS' if result.status else 'FAIL'} | {result.name}: {result.message}")
    return 0 if result.status else 1

def get_route(args):
    """EID 路由配置生成 - Python 实现（读 /home/source_dest_ips.txt 写 /lib/route.conf）"""
    from sprinkler.utils.get_route_eid import main as generate_route_conf
    return generate_route_conf()



def sync_time(args):
    from sprinkler.auto.base.rtc_sync import rtc_sync
    rtc_sync(args)
    

def update_code(args):
    # 检查是否使用了 -update-code 但没有提供必要的 IP 参数
    if args.update_code and (not args.host_ip or not args.proxy_ip):
        print("ERROR: 使用 -update-code 时必须同时提供 --host-ip 和 --proxy-ip 参数")
        sys.exit(1)
        
    host_ip = ""
    if hasattr(args, 'host_ip'):
        host_ip = args.host_ip

    proxy_ip = ""
    if hasattr(args, 'proxy_ip'):
        proxy_ip = args.proxy_ip
        
    command = [
        'bash', 
        str(package_path() / "utils" / "update_code.sh"),
        '--host-ip', host_ip,
        '--proxy-ip', proxy_ip
    ]
    process = subprocess.Popen(command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()

# cpu1213/npu1213/sfu1260通用检查
# 框内 d-u-d 链路查询
def query_path(args):
    """框内 d-u-d 链路查询 - 当前缺少实现，明确报错而不是抛裸 traceback"""
    src_device_name, src_unit_id, src_port_id, dest_ip = args.query_path
    try:
        from sprinkler.auto.runtime.general.query_path import query_path_execute
    except ImportError:
        print("ERROR: -query-path 功能尚未实现（缺少 sprinkler/auto/runtime/general/query_path.py）")
        print(f"       收到参数: device={src_device_name} unit={src_unit_id} "
              f"port={src_port_id} dest_ip={dest_ip}")
        return 1
    return query_path_execute(src_device_name, src_unit_id, src_port_id, dest_ip)

def cluster_eth(args):
    from sprinkler.auto.base.cluster_eth import cluster_eth
    cluster_eth(args)

def env_check(args):
    """高性能模式检查 - Python实现（单机）"""
    from sprinkler.auto.base.enable_env_correct import check_local_env, check_local_power
    from sprinkler.base.log import RunLog

    run_log = RunLog(args, "ENV-check")
    run_log.info("开始单机环境检查")

    results = check_local_env(device_count=getattr(args, 'device_count', 8))
    for chk in results:
        if chk.status:
            run_log.ok(f"  {chk.name}: {chk.message}")
        else:
            run_log.fail(f"  {chk.name}: {chk.message}")

    if hasattr(args, 'test_rate_file') and args.test_rate_file:
        power_result = check_local_power(args.test_rate_file, getattr(args, 'device_count', 8))
        if power_result.status:
            run_log.log("OK", f"  {power_result.name}: {power_result.message}")
        else:
            run_log.fail(f"  {power_result.name}: {power_result.message}")
    else:
        run_log.info("  未提供 -test-rate-file，跳过功率检查")


def cluster_env_check(args):
    """集群范围环境检查"""
    from sprinkler.auto.base.enable_env_correct import cluster_env_check as _cluster_env_check
    _cluster_env_check(args, check_power=getattr(args, 'check_power', False))
    

def auto_exec():
    """Run all auto checks selected by CLI flags. Returns aggregated exit code (0 = all passed).

    Each check runs independently; a non-zero rc from a check that returns int is aggregated
    via OR so a failing check never skips subsequent ones. Currently only ``version_query``
    propagates its exit code (per USAGE_GUIDE.md return-code contract).
    """
    args = get_args()
    rc = 0
    if args.stop_urma:
        stop_urma(args)
    if args.device_online:
        device_online(args)
    if args.check_firewall:
        stop_firewall(args)
    if args.cpu_hugepage:
        config_hugepage(args)
    if args.check_eth:
        check_eth()
    if args.cluster_eth:
        cluster_eth(args)
    if args.npu_set_device:
        npu_set_device(args)
    if args.npu_check_resource:
        npu_check_resource(args)
    if args.npu_kill:
        npu_kill(args)
    if args.single_operator:
        single_operator(args)
    if args.mpirun_bw:
        mpirun_bandwidth(args)
    if args.pta_test:
        pta_test(args)
    if getattr(args, 'pta_cluster', False):
        pta_cluster(args)

    if args.sync_time:
        sync_time(args)
    if args.gen_route:
        print('[warning]不再需要执行-gen-route，直接执行-no-pass配置免密，或-npu-ip查看;260630后将删除此命令')
        print('[warning]260630后将删除此命令')
    if args.npu_ip:
        args.mode = "h2d"
        from sprinkler.auto.bandwidth.planning.eid_pairs import get_eid_pairs
        get_eid_pairs(args)
    if args.get_power:
        get_power(args)
    if args.update_code:
        update_code(args)
    if args.cluster_mpi:
        cluster_mpi(args)
    if getattr(args, 'sfu_check', False) or getattr(args, 'npu_check', False) or getattr(args, 'cpu_check', False):
        from sprinkler.auto.runtime.check.runtime_check import runtime_check
        runtime_check(args)
    if args.get_route:
        get_route(args)
    if args.enable_env:
        enable_env(args)
    if args.ssh_check:
        from sprinkler.auto.runtime.general import ssh_check
        ssh_check.execute(args)
    if args.pairs:
        from sprinkler.auto.bandwidth.planning.eid_pairs import get_eid_pairs
        get_eid_pairs(args)
    if args.ping_check:
        from sprinkler.auto.bandwidth.planning.ping_check import ping_check_execute
        ping_check_execute(args)
    if args.check_npu_stream_control:
        from sprinkler.auto.base import check_npu_stream_control
        check_npu_stream_control.check_npu_stream_control_execute(args)
    if args.urma_cmd:
        from sprinkler.auto.bandwidth.core.command_preview import urma_cmd_execute
        urma_cmd_execute(args)
    if args.urma_bandwidth or args.urma_bandwidth == []:
        from sprinkler.auto.bandwidth.core.runner import urma_bandwidth_execute
        urma_bandwidth_execute(args)
    if args.uboe_env_check:
        from sprinkler.auto.bandwidth.uboe.env import uboe_env_execute
        uboe_env_execute(args)
    if args.cluster_bw or args.cluster_bw == []:
        from sprinkler.auto.bandwidth.core.cluster import cluster_bw_execute
        cluster_bw_execute(args)
    if args.no_pass:
        from sprinkler.auto.base.no_pass import no_pass_check
        no_pass_check(args)
    if args.cluster_nopass:
        from sprinkler.auto.base.cluster_nopass import cluster_nopass
        cluster_nopass(args)
    if args.env_check:
        env_check(args)
    if args.cluster_env_check:
        cluster_env_check(args)

    # bmc相关检查
    if args.check_version:
        from sprinkler.auto.runtime.check.check_version import check_version_exec
        check_version_exec(args)
    if getattr(args, "version_query", False):
        from sprinkler.checker.version.query import version_query_execute
        rc = version_query_execute(args) or rc
    if getattr(args, "status_query", False):
        from sprinkler.checker.status import status_query_execute
        rc = status_query_execute(args) or rc

    # 固件升级
    if getattr(args, "upgrade_init", False) or getattr(args, "upgrade_run", False) \
            or getattr(args, "upgrade_batch", False):
        from sprinkler.checker.upgrade.upgrade_cli import upgrade_exec
        rc = upgrade_exec() or rc

    if args.npu_sol:
        from sprinkler.auto.runtime.general.npu_sol import npu_sol_execute
        npu_sol_execute(args)

    if args.npu_bash:
        args.file = os.path.dirname(_RUN_PATH) + '/sprinkler/auto/runtime/general/npu_sol_cmds.txt'
        from sprinkler.auto.runtime.general.npu_sol import npu_sol_execute
        npu_sol_execute(args)

    if getattr(args, 'npu_sol_smn', False):
        from sprinkler.auto.runtime.general.npu_sol import npu_sol_smn_execute
        npu_sol_smn_execute(args)

    if getattr(args, 'npu_bash_smn', False):
        args.file = os.path.dirname(_RUN_PATH) + '/sprinkler/auto/runtime/general/npu_sol_cmds.txt'
        from sprinkler.auto.runtime.general.npu_sol import npu_sol_smn_execute
        npu_sol_smn_execute(args)

    if args.power_control:
        from sprinkler.auto.runtime.general.power_control import power_control_exec
        power_control_exec(args)

    # 链路查询
    if args.query_path:
        query_path(args)
    
    # svm打流
    if args.svm_bandwidth:
        svm_bandwidth(args)
    run_from_args(args)

    # SMN相关检查

    # 纳管场景检查
    if args.podmanager_check:
        pod_manager_check(args)

    return rc

def pod_manager_check(args):
    """PoDManager纳管条件检查"""
    from sprinkler.auto.podm.manager.podmanager_check import pod_manager_check as pod_check
    return pod_check(args)

