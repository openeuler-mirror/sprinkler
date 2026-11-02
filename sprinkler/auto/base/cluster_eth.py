from sprinkler.base.log import RunLog
from sprinkler.connect.connection_runner import create_runner
from sprinkler.connect.targets import TargetContext
from sprinkler.utils.utils import default_config_dir
from sprinkler.connect.connect import CONNECTION_MODES

GET_IF_COMMAND = "ip route show default | awk '{for(i=1;i<=NF;i++) if($i==\"dev\") print $(i+1)}'"


def extract_interface_names(stdout: str) -> list[str]:
    if not stdout:
        return []
    return stdout.split()


def get_interfaces_from_nodes(args, run_log: RunLog):
    targets = TargetContext(args).get_targets("cpu_os")
    runner = create_runner(args, run_log=run_log, connection_type="ssh_os")
    all_interface_names = []

    try:
        results = runner.run(targets=targets, commands=[GET_IF_COMMAND])
    except Exception as exc:
        run_log.fail_error(CONNECTION_MODES["cpu_os"]["target_method"], exc)
        return []

    for result in results:
        node = result.get("cabinet_name", "") or result.get("host", "unknown")
        number = result.get("target", "").rsplit(":n", 1)[-1]
        if number == result.get("target", ""):
            number = ""
        if result.get("rc", -1) != 0:
            error = result.get("stderr", "执行失败")
            run_log.fail(f"[{node}] OS{number}: {error}")
            continue

        stdout = result.get("stdout") or ""
        interfaces = extract_interface_names(stdout)
        if interfaces:
            all_interface_names.extend(interfaces)
            run_log.info(f"[{node}] OS{number}: {', '.join(interfaces)}")

    return sorted(set(all_interface_names))


def cluster_eth(args):
    run_log = RunLog(args, "cluster-eth")
    run_log.info("开始检查集群网卡配置")

    interface_names = get_interfaces_from_nodes(args, run_log)
    iface_str = ",".join(interface_names) if interface_names else ""

    if iface_str:
        run_log.info(f"集群网卡信息汇总: {iface_str}")
        run_log.info(f"导入环境变量方式: export HCCL_SOCKET_IFNAME={iface_str}")
    else:
        run_log.fail("未发现任何网卡")
        
