"""
``Sprinkler `` provides a limited set of the functionality as ``cpupower, hccl-tools, etc``.

Uasge
--------

1. Verify CPU configuration
++++++++++++++++++++++++++

To start verification
::

    sprinkler --cpu
    or
    sprinkler -C

2. Verify Network configuration
+++++++++++++++++++++++++++++++

To start verification
::

    sprinkler --network
    or
    sprinkler -N
"""
import re
import sys
import argparse
from argparse import ArgumentParser
import ipaddress
import logging
from sprinkler.utils.utils import argparse_positive_int, validate_port

arguments = None


class CheckChipAction(argparse.Action):
    def __init__(self, option_strings, dest, **kwargs):
        super().__init__(option_strings, dest, nargs="*", **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        if len(values) not in (3,):
            parser.error(f"{option_string} expects 3 args: <check_mode(cpu/npu/sfu)> <slot_id> <chip_id>")

        check_mode = values[0]
        if check_mode not in ('cpu', 'npu', 'sfu'):
            parser.error(f'无效的check_mode: {check_mode}, 可选：cpu/npu/sfu')
        slot_id = values[1]
        chip_id = values[2]

        setattr(namespace, self.dest, (check_mode, slot_id, chip_id))


class QueryPathAction(argparse.Action):
    def __init__(self, option_strings, dest, **kwargs):
        super().__init__(option_strings, dest, nargs="*", **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        if len(values) not in (4,):
            parser.error(f"{option_string} expects 4 args: <src_device_name> <src_unit_id> <src_port_id> <dest_ip>")

        src_device_name = values[0]
        src_unit_id = values[1]
        src_port_id = values[2]
        dest_ip = values[3]

        setattr(namespace, self.dest, (src_device_name, src_unit_id, src_port_id, dest_ip))


# ============================================================
# Argument Groups
# ============================================================

class ArgumentGroups:
    @staticmethod
    def add_verify(parser):
        group = parser.add_argument_group(title='System Verification')
        group.add_argument("-C", "--cpu", action='store_true', help="Verify CPU Configuration")
        group.add_argument("-N", "--network", action='store_true', help="Verify Network Configuration")
        group.add_argument("-D", "--device", action='store_true', help="Verify NPU Configuration")
        group.add_argument("-S", "--storage", action='store_true', help="Verify Storage Configuration")
        group.add_argument("-G", "--group", action='store_true', help="Verify Group Configuration")
        group.add_argument("-PM", "--podmanager", action='store_true', help="Verify PodManager Configuration")
        group.add_argument("-T", "--thirdparty", action='store_true', help="Verify Thirdparty Configuration")
        group.add_argument("-W", "--mindcluster", action='store_true', help="Verify MindCluster Configuration")
        group.add_argument("-L", "--log", action='store_true', help="Verify Log Configuration")
        group.add_argument("-MDIE", "--MindIE", action="store_true", help="Check the MindIE Configuration")
        group.add_argument("-A", "--all", action='store_true', help="Verify All Configuration")

    @staticmethod
    def add_log(parser):
        group = parser.add_argument_group(title='Log Operations')
        group.add_argument("-P", "--plog", action='store_true', help="Open Plog Func")
        group.add_argument("-error", "--plog_error", action='store_true', help="Find ERROR In Plog")
        group.add_argument("-PA", "--plog_error-analysis", action='store_true', help="Analysis ERROR In Plog")
        group.add_argument("-log_save", "-log-save", action='store_true', help="Save logs")
        group.add_argument("-log_clean", "-log-clean", action='store_true', help="Clean pod logs")
        group.add_argument("-log-qemu-save", action='store_true', help="Save qemu logs")
        group.add_argument("-log-qemu-clean", action='store_true', help="Clean qemu logs")
        group.add_argument("-plevel", "--plog_level", type=int, choices=[1, 2, 3], default=1,
                           help="plog search level: 1-plog, 2-debug, 3-log (default: 1)")
        group.add_argument("-n", "--number", type=int, default=15, help="ERROR numbers (default: 15)")
        group.add_argument("-check_log", "-check-log", action='store_true', help="Check logs")
        group.add_argument("--log-path", "--log_path", type=str, default=None, help="Get Log Path")
        group.add_argument('--log-collect', '--log_collect', action='store_true', help='Collect plog of all nodes')
        group.add_argument('--log-analysis', '--log_analysis', action='store_true', help='Analyze plog of all nodes')
        group.add_argument('--start-time', '--start_time', type=str, default="", help="Log Start time")
        group.add_argument("-collect-logs", "-collect_logs", action='store_true',
                           help="Collect all logs via connect module with superpod-table credentials")
        group.add_argument("--collect-root", "--collect_root", dest="collect_root", type=str, default=None,
                           help="Root directory for log collection runs (default: ./debug_log)")
        group.add_argument("-log-type", "-log_type", dest="log_type", type=str, default="all",
                           help="Scope for -collect-logs, comma-separated: "
                                "cpu_os (local dmesg/messages), npu_os (NPU dmesg/messages via ssh), "
                                "plog (Ascend log/atrace), msnpureport, "
                                "npu_bmc (NPU BMC 1213 UB logs), cpu_bmc (CPU BMC 1213 UB logs), "
                                "all (default)")

    @staticmethod
    def add_env_check(parser):
        group = parser.add_argument_group(title='Environment Check')
        group.add_argument("-auto", "--auto", nargs='?', const="L2", choices=["L1", "L2"], metavar="LEVEL",
                           help="Open Automatic Environmental Health Check Func")
        group.add_argument("-set-rc", "-set_rc", action='store_true', help="Set rc")
        group.add_argument("-enable-env", "-enable_env", action='store_true', help="enable env config is ok")
        group.add_argument("-env-check", "-env_check", action='store_true', help="Check env")
        group.add_argument("-cpu-hugepage", "-cpu_hugepage", action='store_true', help="Configure CPU hugepages memory")
        group.add_argument("-cluster-env-check", "-cluster_env_check", action='store_true',
                           help="Cluster-wide env check (CPU performance mode, board ID, power)")
        group.add_argument("-set-device", "-set_device", dest='npu_set_device', action='store_true',
                           help="Check npu set_device")

    @staticmethod
    def add_device_ops(parser):
        group = parser.add_argument_group(title='Device Operations')
        group.add_argument("-npu-smi", "-npu_smi", action='store_true', help="quick npu smi memory watch")
        group.add_argument("-read-dev", "-read_dev", action='store_true', help="Read device")
        group.add_argument("-write-dev", "-write_dev", action='store_true', help="Write device")
        group.add_argument("-reg-rw", "-reg_rw", action='store_true', help="Register read/write")
        group.add_argument("-reg-ids", "-reg_ids", type=str, default="0,1,2,3,4,5,6,7", help="Register IDs")
        group.add_argument("-npu-lock", "-npu_lock", action='store_true', help="NPU lock")
        group.add_argument("-check-eth", "-check_eth", action='store_true', help="Check ethernet")
        group.add_argument("-cluster-eth", "-cluster-eth", action='store_true', help="Cluster ethernet check")
        group.add_argument("-check-cpu-mem", "-check_cpu_mem", action='store_true', help="Check CPU memory")
        group.add_argument("-execute-npu", "-execute_npu", action='store_true', help="Execute on NPU")
        group.add_argument("-npu-root", "-npu_root", action='store_true', help="open npu root")
        group.add_argument("-check-chip-delay", "-check_chip_delay", action='store_true', help="Query chip latency on each device")
        group.add_argument("-device-online", "-device_online", action='store_true', help="Check device online")
        group.add_argument("-check-firewall", "-check_firewall", action='store_true', help="Check firewalld/iptables, prompt to stop if active")
        group.add_argument("-reinsert-ipourma-ko", "-reinsert_ipourma_ko", action='store_true',
                           help="Reinsert the ipourma.ko on all devices")
        group.add_argument("-npu-check-resource", "-npu_check_resource", action='store_true',
                           help='check npu-smi info.')
        group.add_argument("-npu-kill", "-npu_kill", action='store_true',
                           help='Kill npu process')

    @staticmethod
    def add_urma(parser):
        group = parser.add_argument_group(title='URMA Operations')
        group.add_argument("-urma-show", "-urma_show", action='store_true', help="urma-admin-show and check eid")
        group.add_argument("-urma-perftest", "-urma_perftest", nargs=2, metavar=("CLIENT_RANGE", "SERVER_RANGE"),
                           help="Generate uram-perftest commands for CLIENT_RANGE -> SERVER_RANGE")
        group.add_argument("-urma-fix", "-urma_fix", action='store_true', help="Generate register devmem write command")
        group.add_argument("-stop-urma", "-stop_urma", action='store_true', help="Stop NPU urma process")
        group.add_argument("-urma-bandwidth", dest="urma_bandwidth", nargs="*", default=None,
                           help="run intra-board URMA bandwidth traffic")
        group.add_argument("-cluster-bw", dest="cluster_bw", nargs="*", default=None,
                           help="run xboard traffic: server-rank [client-node-selector]")
        group.add_argument("-pairs", dest="pairs", action="store_true", help="print URMA EID pairs")
        group.add_argument("-pair", dest="pair_spec", default="",
                           help="custom intra-board D2D pairs, e.g. 0-1,2-3,4-5")
        group.add_argument("-xboard-pair", dest="xboard_pair_spec", default="",
                           help="custom cross-board D2D pairs, e.g. 0-1,2-3,4-5")
        group.add_argument("-mode", default="all", choices=["h2d", "d2h", "d2d", "all", "xd2d", "ubg", "uboe", "uboe-xboard"],
                           help="bandwidth plan mode")
        group.add_argument("-d2d-main", type=int, default=None, help="main NPU rank for D2D")
        group.add_argument("-ping-check", dest="ping_check", action="store_true", help="run ping checks from URMA EID pairs")
        group.add_argument("-stream-control", dest="check_npu_stream_control", action="store_true",
                           help="check NPU stream control register via devmem")
        group.add_argument("-urma-cmd", dest="urma_cmd", action="store_true", help="print URMA listener/client commands")
        group.add_argument("-d2d-mode", choices=["many-to-one", "one-to-many", "alltoall"], default="many-to-one",
                           help="D2D pair expansion mode (alltoall: every NPU sends to every other NPU; "
                                "add -single to drop -B)")
        group.add_argument("-same-plane", dest="same_plane", action="store_true", help="use same-plane rank mapping")
        group.add_argument("-link-mode", default="all", help="xboard link selector: all, both, l0, or l1")
        group.add_argument("-urma-mode", default="write_bw", help="urma_perftest mode")
        group.add_argument("-urma-opts", default=None,
                           help="extra options appended to urma_perftest; defaults follow -urma-mode, "
                                "*_bw uses \"-R 4096 -T 4096 -C 16384 -p 0 -y -s 16777216 -L -l 256 -Q 256 "
                                "--tp_aware --ctp -B --hugepage_size 2MB --enable_ipv6\", "
                                "*_lat uses \"-n 10 -s 8 -I 128 --tp_aware --priority 0 --enable_ipv6\"")
        group.add_argument("-eid-mode", choices=["ipv6", "ipv4"], default="ipv4", help="H2D/D2H URMA address mode")
        group.add_argument("-all2all", dest="all2all", action="store_true", help="use all client ranks against selected server ranks")
        group.add_argument("-duration", type=argparse_positive_int, default=30, help="URMA measurement duration seconds")
        group.add_argument("-server-rank", dest="server_rank", help="server NPU rank range for -cluster-bw")
        group.add_argument("-uboe-env-check", dest="uboe_env_check", action="store_true",
                           help="UBOE pre-check: eth port / eid / mtu on each card's device side")
        group.add_argument("-bond-ip", dest="bond_ip_map", default="",
                           help="UBOE bond-ip override map, e.g. 0=10.10.25.2,1=10.10.32.2 (rank=ip); optional -- auto-discovered per card when omitted")
        group.add_argument("-uboe-port", type=argparse_positive_int, default=62600,
                           help="UBOE urma_perftest port (default: 62600)")
        group.add_argument("-uboe-opts", default=None,
                           help="UBOE urma_perftest fixed opts (overridable; defaults follow "
                                "-urma-mode, *_bw uses \"-s 67108864 --tp_aware --hugepage_size 2MB "
                                "--dscp 33 -y\", "
                                "*_lat uses \"-n 10 -s 8 -I 128 --tp_aware --priority 0\"")
                                

    @staticmethod
    def add_cluster(parser):
        group = parser.add_argument_group(title='Cluster Management')
        group.add_argument("-cluster-nopass", "-cluster_nopass", action="store_true",
                           help="Cluster SSH nopass config. Use with -sp, -ca, -node")
        group.add_argument("-no-pass", "-no_pass", action='store_true', help="SSH key automation for multiple device pairs")
        group.add_argument("-sfu-no-pass", "-sfu_no_pass", action='store_true', help="SSH key automation for SFU device pairs")
        group.add_argument("-cluster-mpi", "-cluster_mpi", action="store_true",
                           help="Cluster MPI bandwidth test. Use with -sp, -cl, -node, -test-type, -hccl-test")
        group.add_argument("-single", action="store_true",
                           help="Single mode: execute mpirun on each host separately")
        group.add_argument("-parallel-cabinet", "-pc", action="store_true", dest="parallel_cabinet",
                           help="Parallel cabinet mode: execute mpirun on each cabinet in parallel")
        group.add_argument("-test-type", type=str, default="alltoall_test", dest="test_type",
                           help="HCCL test operator type (default: alltoall_test)")
        group.add_argument("-hccl-test", type=str, default="aicpu_ts", dest="hccl_test",
                           help="HCCL communication mode (default: aicpu_ts)")
        group.add_argument("-master", type=str, default=None,
                           help="Master host IP for cluster mode")
        group.add_argument("-hccl-buffer", type=int, default=1024, dest="hccl_buffer",
                           help="HCCL_BUFFSIZE value in MB (default: 1024)")
        group.add_argument("-mpi-n", type=int, default=8, help="Number of MPI processes (default: 8)")

    @staticmethod
    def add_pta(parser):
        group = parser.add_argument_group(title='PTA Test')
        group.add_argument("-pta-test", "-pta_test", action="store_true", help="PTA算子测试(单机)")
        group.add_argument("-pta-cluster", "-pta_cluster", action="store_true", help="PTA集群测试")
        group.add_argument("-op-type", "-op_type", type=str, default="alltoall",
                           choices=["allreduce", "alltoall", "gpt"], help="PTA算子测试类型 (默认: alltoall)")
        group.add_argument("-master-port", "-master_port", type=int, default=20022,
                           help="PTA集群torchrun master端口 (默认: 20022)")
        group.add_argument("-timeout", type=int, default=60, help="PTA测试超时时间(秒) (默认: 60)")
        group.add_argument("-nproc-per-node", "-nproc_per_node", type=int, default=0,
                           help="每节点进程数, 0=自动检测 (默认: 0)")

    @staticmethod
    def add_network(parser):
        group = parser.add_argument_group(title='Network & Route')
        group.add_argument("-get-route", "-get_route", action='store_true', help="Restore the routing configuration")
        group.add_argument("-gen-route", "-gen_route", action='store_true', help="Auto gen route.conf")
        group.add_argument("-sync-time", "-sync_time", action='store_true', help="Sync system time from RTC")
        group.add_argument("-test-snr", "-test_snr", action='store_true', help="Auto test snr")
        group.add_argument("-npu-ip", "-npu_ip", action='store_true', help="Show all npu ip")
        group.add_argument("-p2p-sendrecv", "-p2p_sendrecv", action='store_true', help="Auto check p2p send/recv bandwidth")

    @staticmethod
    def add_utils(parser):
        group = parser.add_argument_group(title='Utilities')
        group.add_argument("-fix-ranktable", "-fix_ranktable", action='store_true',
                           help="Fix ranktable for logic_devid <-> udevid")
        group.add_argument("-full-lane", "-full_lane", action='store_true', help="Enable full lane configuration")
        group.add_argument("-get-power", "-get_power", action='store_true', help="get rated power")
        group.add_argument("-get-root", "-get_root", action='store_true', help="Get root configuration")
        group.add_argument("-test-rate-file", type=str, help="Power test file path for hp-check")
        group.add_argument("-jetty", action='store_true', help="jetty test")
        group.add_argument("-jetty-model", "-jm", type=str, choices=["cpu", "npu"], default="cpu",
                           help="Select the jetty mode")
        group.add_argument("-udma-id", type=int, default=3, help="Set udma id for jetty test")
        group.add_argument("-ssh-ip", type=str, help="Set ssh ip for jetty test")
        group.add_argument("-single-operator", "-single_operator", action='store_true', help="Check hccl single operator")
        group.add_argument("-mpirun-bw", "-mpirun_bw", action='store_true', help="Check mpirun bandwidth test")
        group.add_argument("-test-gpt", "-test_gpt", action='store_true', help="Auto test gpt")
        group.add_argument("-update-code", "-update_code", action='store_true', help="Update sprinkler code")
        group.add_argument("-host-ip", "-host_ip", type=str, help="Host IP for update code")
        group.add_argument("-proxy-ip", "-proxy_ip", type=str, help="Proxy IP for update code")

    @staticmethod
    def add_cpu(parser):
        parser.add_argument_group(title='CPU Operations')

    @staticmethod
    def add_runtime(parser):
        group = parser.add_argument_group(title='Runtime Check')
        group.add_argument('-check-version', dest='check_version', action='store_true',
                           help='Batch check BMC version for cpu-bmc, npu-bmc, and sfu-bmc targets')
        group.add_argument('-check-basic-service', dest='check_basic_service', action='store_true',
                           help='Batch check basic service status for SMN OS')
        group.add_argument('-sfu-check', dest='sfu_check', action='store_true', help='check sfu 1260.')
        group.add_argument('-npu-check', dest='npu_check', action='store_true', help='check npu 1213.')
        group.add_argument('-cpu-check', dest='cpu_check', action='store_true', help='check cpu 1213.')
        group.add_argument('-check-item', dest='check_item', type=str, default=None,
                           help='Select the items to be checked, supported: credit/port/link/config/card/disk/all')
        group.add_argument('-parallel', dest='parallel', type=int, default=8,
                           help='Parallel workers for runtime check')

    @staticmethod
    def add_podm(parser):
        group = parser.add_argument_group(title='PoDM Check')
        group.add_argument("-podmanager-check", dest='podmanager_check', action='store_true',
                           help=" PodManager check for BMC MeSwitch UBM")
        group.add_argument('-check-superpod', dest='check_superpod', action='store_true',
                           help='Batch check superpod info for SMN OS')
        group.add_argument('-check-port-50080', dest='check_port_50080', action='store_true',
                           help='Batch check port 50080 on CPU/NPU/SFU BMC')
        group.add_argument('-check-cert', dest='check_cert', action='store_true',
                           help='Batch check cert for SMN OS')

    @staticmethod
    def add_bmc(parser):
        group = parser.add_argument_group(title='BMC Check')
        group.add_argument('-npu-sol', '-npu_sol', action='store_true',
                           help="SSH to NPU BMC, enter each NPU SOL port, and run commands")
        group.add_argument('-npu-bash', '-npu_bash', action='store_true', help="Set npu bash")
        group.add_argument('-npu-sol-smn', '-npu_sol_smn', action='store_true',
                           help="SSH to PodManager, loginBMC NPU slot, enter each NPU SOL port, and run commands")
        group.add_argument('-npu-bash-smn', '-npu_bash_smn', action='store_true',
                           help="SSH to PodManager, loginBMC NPU slot, enter NPU SOL to setup NPU bash (ssh)")
        group.add_argument('-power-control', dest='power_control', action='store_true',
                           help='Batch control BMC power (on/off/status) for cpu-bmc, npu-bmc, and sfu-bmc targets')

    @staticmethod
    def add_path(parser):
        group = parser.add_argument_group(title='Path Check')
        group.add_argument('-query-path', '-query_path', action=QueryPathAction, metavar="PATH_ARGS",
                           help="Query d-u-d path")

    @staticmethod
    def add_smn(parser):
        group = parser.add_argument_group(title='SMN Serial Port Run Cmd')
        group.add_argument('-cpu-1213-smn', action='store_true', dest='cpu_1213_smn',
                           help='SSH to PodManager, loginUBM CPU slot, run cmd')
        group.add_argument('-npu-1213-smn', action='store_true', dest='npu_1213_smn',
                           help='SSH to PodManager, loginUBM NPU slot, run cmd')
        group.add_argument('-sfu-com-smn', action='store_true', dest='sfu_com_smn',
                           help='SSH to PodManager, loginUBM SFU slot, run cmd')
        group.add_argument('-cpu-bmc-smn', action='store_true', dest='cpu_bmc_smn',
                           help='SSH to PodManager, loginBMC CPU slot, run cmd')
        group.add_argument('-npu-bmc-smn', action='store_true', dest='npu_bmc_smn',
                           help='SSH to PodManager, loginBMC NPU slot, run cmd')
        group.add_argument('-sfu-bmc-smn', action='store_true', dest='sfu_bmc_smn',
                           help='SSH to PodManager, loginBMC SFU slot, run cmd')
        group.add_argument('-index', type=str, default=None,
                           help='device index range, e.g. 0-7 or 0,3,7 (default: all)')

    @staticmethod
    def add_config(parser):
        group = parser.add_argument_group(title='Configuration')
        group.add_argument('-superpod-conf', action='store_true', help="Create config file for of superpod")
        group.add_argument('-sp-info', action="store_true", help="print superod infomation")
        group.add_argument('-modify-pwd', action='store_true',
                           help="Modify password of CPU/NPU/SFU in superpod_n.xlsx")

    @staticmethod
    def add_db(parser):
        group = parser.add_argument_group(title='Database (Cluster Ledger)')
        group.add_argument('-db-import', '-db_import', action='store_true',
                           help="Import superpod_<sp>.xlsx into cluster/cabinet/blade ledger")
        group.add_argument('-db-xlsx', '-db_xlsx', type=str, default=None,
                           help="Excel ledger path; default sprinkler/data/superpod_<sp>.xlsx")
        group.add_argument('-db-overwrite', '-db_overwrite', action='store_true',
                           help="Overwrite existing cluster tree in the same transaction")
        group.add_argument('-db-name', '-db_name', type=str, default=None,
                           help="Cluster name; default superpod_<sp>")
        group.add_argument('-db-stats', '-db_stats', action='store_true',
                           help="Show ledger row counts (cluster/cabinet/blade by type)")
        group.add_argument('-db-list', '-db_list', action='store_true',
                           help="List clusters with cabinet counts")
        group.add_argument('-db-keyword', '-db_keyword', type=str, default=None,
                           help="Keyword filter for -db-list")
        group.add_argument('-db-cab', '-db_cab', type=str, default=None,
                           help="Cabinet name to list blades, e.g. C02-A06 (needs -sp)")

    @staticmethod
    def add_connect(parser):
        group = parser.add_argument_group(title='SSH Connection')
        group.add_argument('-xlsx', type=str, default=None,
                           help="explicit superpod_<sp>.xlsx target config path or config directory")
        group.add_argument('-cmd', action='append', default=None, help="command to run; can be repeated")
        group.add_argument('-file', help="text command file, one command per non-empty line; not xlsx config")
        group.add_argument('-show-output', action='store_true',
                           help="print command stdout/stderr to terminal and run.log")
        group.add_argument('-ssh-check', dest='ssh_check', action='store_true', help="check target connectivity")
        group.add_argument("-target-type", dest="target_type",
                           choices=["all", "cpu-os", "cpu-bmc", "npu-bmc", "npu-1213",
                                    "sfu-bmc", "sfu-com", "roce-bmc", "roce-com",
                                    "bmc", "sfu", "roce"],
                           default="cpu-os", help="target type for -ssh-check")
        group.add_argument('-cpu-os', dest='cpu_os', action='store_true', help="execute commands on CPU OS targets")
        group.add_argument('-cpu-bmc', dest='cpu_bmc', action='store_true', help="execute commands on CPU BMC targets")
        group.add_argument('-cpu-1213', dest='cpu_1213', action='store_true', help="execute commands on CPU 1213 targets")
        group.add_argument('-npu-bmc', dest='npu_bmc', action='store_true', help="execute commands on NPU BMC targets")
        group.add_argument('-npu-1213', dest='npu_1213', action='store_true', help="execute commands on NPU 1213 targets")
        group.add_argument('-sfu-bmc', dest='sfu_bmc', action='store_true', help="execute commands on SFU BMC targets")
        group.add_argument('-sfu-com', dest='sfu_com', action='store_true', help="execute commands on SFU COM targets")
        group.add_argument('-roce-bmc', dest='roce_bmc', action='store_true', help="execute commands on RoCE BMC targets")
        group.add_argument('-roce-com', dest='roce_com', action='store_true', help="execute commands on RoCE COM targets")
        group.add_argument('-smn-os', dest='smn_os', action='store_true', help="execute commands on SMN OS targets")
        group.add_argument('-smn-bmc', dest='smn_bmc', action='store_true', help="execute commands on SMN BMC targets")
        group.add_argument('-sol-ports', dest='sol_ports', type=str, default=None,
                           help="NPU SOL ports to enter, e.g. 3 or 3,5 "
                                "(default 3,5,7,9,11,13,15,17)")
        group.add_argument('-node', type=str, default=None,
                           help="Node spec: {s0:[c1:0-7]} or IP;user;pwd (e.g. 10.0.0.1,10.0.0.2;root;pwd)")
        group.add_argument('-rank', type=str, default='0-7', help="NPU rank range, for example 0-7")
        group.add_argument('-sp', type=str, default='', help="Superpod number (0-f)")
        group.add_argument('-ca', type=str, default='',
                           help="Cabinet number (0-f, can be range like 0-3 or list like 0,1,2)")
        group.add_argument("-ip", help="direct target IPs separated by commas; alternative to -sp/-ca/-node/-xlsx")
        group.add_argument("-user", default="root",
                           help="default xlsx user, or one shared/per-IP user for -ip targets")
        group.add_argument("-pwd", default="",
                           help="target passwords: one shared value or one comma-separated value per -ip")
        group.add_argument("-no-fix-sftp", "-no_fix_sftp", dest="no_fix_sftp", action="store_true",
                           help="skip auto-fixing remote sshd_config Subsystem sftp "
                                "when connecting to MeSwitch via SFTP (use when sshd_config is already prepared)")

    @staticmethod
    def add_svm(parser):
        group = parser.add_argument_group(title='SVM Bandwidth Check')
        group.add_argument("-svm-bandwidth", "-svm_bandwidth", action="store_true",
                           help="Auto check svm bandwidth.")
        group.add_argument("-svm-path", "-svm_path", type=str, help="svm testcase dir")
        group.add_argument("-cycles", type=int, default=0, help="svm bandwidth check case loop count")
        group.add_argument("-dst-open-num", "-dst_open_num", type=int, default=2, help="destination process open times")
        group.add_argument("-ipc-size", "-ipc_size", type=int, default=2097152, help="shared memory size")
        group.add_argument("-incrememt-size", "-incrememt_size", type=int, default=2097152, help="offset size")
        group.add_argument("-op-size", "-op_size", type=int, default=2097152, help="opption size")
        group.add_argument("-pg-type", "-pg_type", type=int, choices=[0, 1, 2], default=1, help="page size")
        group.add_argument("-device-num", "-device_num", type=int, default=8, help="device num")
        group.add_argument("-port", type=validate_port, default=88888, help="device num")
        group.add_argument("-cann-path", "-cann_path", type=str, default="/usr/local/Ascend/cann/set_env.sh",
                           help="svm multiple devices config")

    @staticmethod
    def add_version_query(parser):
        group = parser.add_argument_group(title='Version Query')
        group.add_argument('-version-query', '-version_query', dest='version_query', action='store_true',
                           help='Batch query device version info (BMC via Redfish + OS via SSH) and generate report')
        group.add_argument('-component-filter', '-component_filter', dest='component_filter', type=str, default='',
                           help='component filter, comma-separated, e.g. OS,BMC,BIOS,CPLD,CSR,NPU,CC,CFG')
        group.add_argument('-query-mode', '-query_mode', dest='query_mode',
                           choices=['redfish', 'ssh', 'frontend', 'hybrid'], default='hybrid',
                           help='query channel: redfish (BMC only) / ssh (OS only) / frontend (1213/1260 only) / hybrid (all)')
        group.add_argument('-expected-versions', '-expected_versions', dest='expected_versions', type=str, default=None,
                           help='expected version manifest JSON path, for compliance check')
        group.add_argument('-query-timeout', '-query_timeout', dest='query_timeout',
                           type=argparse_positive_int, default=30, help='per-target query timeout seconds')
        group.add_argument('-report-format', '-report_format', dest='report_format',
                           choices=['html'], default='html', help='version report output format (currently only html)')
        group.add_argument('-compliance-only', '-compliance_only', dest='compliance_only', action='store_true',
                           help='only run compliance check against existing report, skip device query')
        group.add_argument('-dry-run', '-dry_run', dest='dry_run', action='store_true',
                           help='print query plan only, do not connect devices')

    @staticmethod
    def add_status_query(parser):
        group = parser.add_argument_group(title='Device Status Query')
        group.add_argument('-status-query', '-status_query', dest='status_query', action='store_true',
                           help='Batch query device status (online/health/power/UID via BMC Redfish) '
                                'and generate results.csv')

    @staticmethod
    def add_upgrade(parser):
        group = parser.add_argument_group(title='Firmware Upgrade')
        group.add_argument('-upgrade-init', '-upgrade_init', dest='upgrade_init',
                           action='store_true',
                           help='Initial deployment upgrade: BMC + UBM via SOL + Telnet crack')
        group.add_argument('-upgrade-run', '-upgrade_run', dest='upgrade_run',
                           action='store_true',
                           help='Daily upgrade: Redfish-first with SSH CLI fallback')
        group.add_argument('-upgrade-batch', '-upgrade_batch', dest='upgrade_batch',
                           action='store_true',
                           help='Batch upgrade: cluster/cabinet scope with parallel control')
        group.add_argument('-upgrade-components', '-upgrade_components',
                           dest='upgrade_components', type=str, default='',
                           help='Component filter: ibmc,bios,cpld,npu_firmware,cc,cfg,telnet_crack')
        group.add_argument('-upgrade-firmware-dir', '-upgrade_firmware_dir',
                           dest='upgrade_firmware_dir', type=str, default=None,
                           help='Local firmware package directory')
        group.add_argument('-upgrade-dry-run', '-upgrade_dry_run',
                           dest='upgrade_dry_run', action='store_true',
                           help='Print upgrade plan only, do not execute')
        group.add_argument('-upgrade-timeout', '-upgrade_timeout',
                           dest='upgrade_timeout', type=int, default=1800,
                           help='Per-target upgrade timeout in seconds (default: 1800)')
        group.add_argument('-cluster-id', '-cluster_id',
                           dest='cluster_id', type=int, default=None,
                           help='Cluster ID from database for batch upgrade')

    @staticmethod
    def add_hixl(parser):
        """HIXL Benchmark 命令组"""
        group = parser.add_argument_group(title='HIXL Benchmark')

        # 主命令
        group.add_argument('-hixl-bandwidth',
                           dest='hixl_bandwidth', action='store_true',
                           help='Run HIXL communication benchmark (单边通信打流)')

        # HIXL 路径参数
        group.add_argument('-hixl-path',
                           dest='hixl_path', type=str, default=None,
                           help='HIXL 仓库根目录路径（覆盖配置文件中的 HIXL_PATH）')

        # --- 传输方向参数（复用 -rank，使用 -hixl-mode） ---
        group.add_argument('-hixl-mode',
                           dest='hixl_mode', type=str, default='D2rD',
                           choices=['D2rD', 'rD2D', 'D2rH', 'rH2D', 'H2rH', 'rH2H', 'H2rD', 'rD2H', 'all'],
                           help='传输方向 (default: D2rD；也可通过 -mode 传入)')

        # --- 拓扑参数 ---
        group.add_argument('-hixl-pattern',
                           dest='hixl_pattern', type=str, default='pairwise',
                           choices=['pairwise', 'one_to_many', 'one-to-many', 'many_to_one', 'many-to-one'],
                           help='通信拓扑模式 (default: pairwise)')
        group.add_argument('-hixl-num-targets',
                           dest='hixl_num_targets', type=int, default=None,
                           help='Target lane 数量 (one_to_many target 上限 / initiator 必填)')
        group.add_argument('-hixl-num-initiators',
                           dest='hixl_num_initiators', type=int, default=None,
                           help='Initiator lane 数量 (many_to_one 使用)')
        group.add_argument('-hixl-host',
                           dest='hixl_host', type=str, default='127.0.0.1',
                           help='本机 IP (default: 127.0.0.1)')
        group.add_argument('-hixl-role',
                           dest='hixl_role', type=str, default=None,
                           choices=['target', 'initiator'],
                           help='双机模式角色: target (服务端) / initiator (客户端)')
        group.add_argument('-hixl-target-host',
                           dest='hixl_target_host', type=str, default=None,
                           help='双机模式 target 端 IP (-hixl-role=initiator 时必填)')
        group.add_argument('-hixl-base-port',
                           dest='hixl_base_port', type=int, default=16000,
                           help='HIXL 基础端口号 (default: 16000)')

        # --- 传输参数 ---
        group.add_argument('-hixl-transport',
                           dest='hixl_transport', type=str, default='uboe',
                           choices=['hccs', 'roce', 'fabric_mem', 'uboe', 'ub_rtp', 'ub', 'all'],
                           help='传输协议 (default: uboe)')
        group.add_argument('-hixl-host-roce-ip',
                           dest='hixl_host_roce_ip', type=str, default=None,
                           help='Host RoCE NIC IP (transport=roce 时需要)')

        # --- 数据大小参数 ---
        group.add_argument('-hixl-block-sizes',
                           dest='hixl_block_sizes', type=str, default='16K:2M',
                           help='Block size 列表/范围 (default: 16K:2M)')
        group.add_argument('-hixl-transfer-size',
                           dest='hixl_transfer_size', type=str, default=None,
                           help='每 block size 步传输字节数 (如 128M)')
        group.add_argument('-hixl-buffer-size',
                           dest='hixl_buffer_size', type=str, default=None,
                           help='分配/注册大小 (如 1G)')
        group.add_argument('-hixl-loops',
                           dest='hixl_loops', type=int, default=5,
                           help='完整 block-step 梯级重复次数 (default: 5)')
        group.add_argument('-hixl-output-dir',
                           dest='hixl_output_dir', type=str, default='comm_benchmark/output',
                           help='输出目录 (default: comm_benchmark/output)')

        # --- HIXL 初始化选项 ---
        group.add_argument('-hixl-option',
                           dest='hixl_option', action='append', default=[],
                           metavar='KEY=VALUE',
                           help='HIXL Initialize() 选项，可重复 (如 -hixl-option LocalCommRes=...)')

        # --- 报告选项 ---
        group.add_argument('-hixl-skip-plot',
                           dest='hixl_skip_plot', action='store_true',
                           help='跳过 PNG 图表生成')
        group.add_argument('-hixl-skip-report',
                           dest='hixl_skip_report', action='store_true',
                           help='跳过 perf.md 报告生成')

        # --- 运行时参数 ---
        group.add_argument('-hixl-peer-wait-s',
                           dest='hixl_peer_wait_s', type=int, default=None,
                           help='Target 端等待 initiator 连接的超时秒数')
        group.add_argument('-hixl-inter-run-delay-s',
                           dest='hixl_inter_run_delay_s', type=int, default=None,
                           help='双机模式下跑之间的等待秒数')
        group.add_argument('-hixl-connect-timeout-ms',
                           dest='hixl_connect_timeout_ms', type=int, default=None,
                           help='Initiator TCP/HIXL 连接超时毫秒数')


# ============================================================
# Parser Entry
# ============================================================

def get_args_parser() -> ArgumentParser:
    parser = ArgumentParser(description="Sprinkler Argument Launcher")
    ArgumentGroups.add_verify(parser)
    ArgumentGroups.add_log(parser)
    ArgumentGroups.add_env_check(parser)
    ArgumentGroups.add_device_ops(parser)
    ArgumentGroups.add_urma(parser)
    ArgumentGroups.add_cluster(parser)
    ArgumentGroups.add_pta(parser)
    ArgumentGroups.add_network(parser)
    ArgumentGroups.add_utils(parser)
    ArgumentGroups.add_cpu(parser)
    ArgumentGroups.add_runtime(parser)
    ArgumentGroups.add_podm(parser)
    ArgumentGroups.add_bmc(parser)
    ArgumentGroups.add_path(parser)
    ArgumentGroups.add_config(parser)
    ArgumentGroups.add_smn(parser)
    ArgumentGroups.add_db(parser)
    ArgumentGroups.add_connect(parser)
    ArgumentGroups.add_svm(parser)
    ArgumentGroups.add_version_query(parser)
    ArgumentGroups.add_status_query(parser)
    ArgumentGroups.add_upgrade(parser)
    ArgumentGroups.add_hixl(parser)
    return parser


def parse_args(args):
    global arguments
    parser = get_args_parser()

    def add_argcomplete_to_bashrc():
        target_line = 'eval "$(register-python-argcomplete sprinkler)"'
        file_path = '/root/.bashrc'

        try:
            with open(file_path, 'r') as f:
                if any(line.rstrip('\n') == target_line for line in f):
                    return
        except FileNotFoundError:
            pass

        with open(file_path, 'a') as f:
            f.write("# ===============sprinkler env===============" + '\n')
            f.write("# sprinkler参数自动补全" + '\n')
            f.write(target_line + '\n')
            f.write("# ===============sprinkler env===============" + '\n')
        print(f'Tip: 自动补全命令已写入~/.bashrc，第一次执行请手动使能eval "$(register-python-argcomplete sprinkler)"')

    try:
        import argcomplete
        argcomplete.autocomplete(parser)
        add_argcomplete_to_bashrc()
    except:
        print("Tip: Install argcomplete for tab completion: pip install argcomplete", file=sys.stderr)

    arguments = parser.parse_args(args)
    return arguments


def get_args():
    global arguments
    return arguments
