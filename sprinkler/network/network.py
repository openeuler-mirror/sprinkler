import os
import subprocess

from sprinkler.arguments import get_args
from sprinkler.utils.utils import _RUN_PATH, find_all_shell


def run_from_args(args):
    roads = {}
    if args.network or args.all:
        roads = [os.path.dirname(_RUN_PATH)+ '/sprinkler/network/firewall_status.sh', \
                os.path.dirname(_RUN_PATH)+ '/sprinkler/network/network_flash.sh', \
                os.path.dirname(_RUN_PATH)+ '/sprinkler/network/network_ip_config.sh', \
                os.path.dirname(_RUN_PATH)+ '/sprinkler/network/tls_status.sh']
        for road in roads:
            print("[Info][Start] Verify Network configuration.")
            command = ['bash', road]
            process = subprocess.Popen(command, 
                                    stdout=subprocess.PIPE, 
                                    stderr=subprocess.STDOUT,
                                    text=True,
                                    bufsize=1)
            for line in process.stdout:
                print(line, end='')
            process.wait()
            print("[Info][End] Verify Network configuration.")


def urma_perftest(args):
    model = "write"
    if hasattr(args, 'model'):
        if args.model == False:
            model = "write"
        else:
            model = str(args.model)
    client_range, server_range= args.urma_perftest
    command = ['bash', os.path.dirname(_RUN_PATH)+ '/sprinkler/network/urma_test_gen.sh', client_range, server_range, model]
    process = subprocess.Popen(command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()


def reg_rw(args):
    if hasattr(args, 'reg_ids'):
        reg_ids = str(args.reg_ids)
    command = ['bash', os.path.dirname(_RUN_PATH)+ '/sprinkler/network/reg_rw.sh', '--reg_ids', str(reg_ids)]
    process = subprocess.Popen(command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()


def fix_ranktable():
    command = ['bash', os.path.dirname(_RUN_PATH)+ '/sprinkler/network/fix_ranktable_from_uda.sh']
    process = subprocess.Popen(command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()


def full_lane():
    command = ['bash', os.path.dirname(_RUN_PATH)+ '/sprinkler/network/lane.sh']
    process = subprocess.Popen(command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()

def sfu_no_pass():
    command = ['bash', os.path.dirname(_RUN_PATH)+ '/sprinkler/network/sfu-no-pass.sh']
    process = subprocess.Popen(command,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT,
                               text=True,
                               bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()

def npu_watch():
    command = ['bash', os.path.dirname(_RUN_PATH)+ '/sprinkler/network/npu_memory_watch.sh']
    process = subprocess.Popen(command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()

def root_get():
    command = ['bash', os.path.dirname(_RUN_PATH)+ '/sprinkler/network/get_root.sh']
    process = subprocess.Popen(command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()

def urma_fix():
    command = ['bash', os.path.dirname(_RUN_PATH)+ '/sprinkler/network/gen_udma_fix_cmds.sh']
    process = subprocess.Popen(command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()
    
    
def network_exec():
    from .execute_npu import execute_npu
    args = get_args()
    run_from_args(args)
    if args.urma_perftest:
        urma_perftest(args)
    if args.fix_ranktable:
        fix_ranktable()
    if args.full_lane:
        full_lane()
    if args.sfu_no_pass:
        sfu_no_pass()
    if args.npu_smi:
        npu_watch()
    if args.get_root:
        root_get()
    if args.urma_fix:
        urma_fix()
    if args.reg_rw:
        reg_rw(args)
    if args.execute_npu:
        execute_npu()
