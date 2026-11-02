import os
import subprocess

from sprinkler.arguments import get_args
from sprinkler.utils.utils import _RUN_PATH, find_all_shell


def run_from_args(args):
    roads = {}
    if args.plog or args.all:
        roads = os.path.dirname(_RUN_PATH)+ '/sprinkler/env/plog/check_log.sh'

        command = ['bash', roads]
        process = subprocess.Popen(command,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                text=True,
                                bufsize=1)
        for line in process.stdout:
            print(line, end='')
        process.wait()



def plog_error_print(args):
    script_args = []
    if hasattr(args, 'plog_level'):
            script_args.extend(['-plevel', str(args.plog_level)])
    if hasattr(args, 'number'):
        script_args.extend(['-n', str(args.number)])
    print("[Info][Start] Find ERROR in logs.")
    command = ['bash', os.path.dirname(_RUN_PATH)+ '/sprinkler/env/plog/plog_search.sh'] + script_args
    process = subprocess.Popen(command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()
    print("[Info][End] Finish find ERROR in logs.")

def plog_error_analysis(args):
    current_file_path = os.path.abspath(__file__)
    current_dir = os.path.dirname(current_file_path)
    log_name = 'plog_error.log'
    log_path = os.path.join(current_dir, log_name)
    if not os.path.isfile(log_path):
        # 还未生成过plog首报错文件，匹配前先调用首报错打印功能，保存待分析的plog日志
        script_args = []
        if hasattr(args, 'plog_level'):
                script_args.extend(['-plevel', str(args.plog_level)])
        if hasattr(args, 'number'):
            script_args.extend(['-n', str(args.number)])
        print("[Info][Start] Find ERROR in logs.")
        command = ['bash', os.path.dirname(_RUN_PATH)+ '/sprinkler/env/plog/plog_search.sh'] + script_args
        process = subprocess.Popen(command,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                text=True,
                                bufsize=1)
        for line in process.stdout:
            print(line, end='')
        process.wait()

    # plog报错匹配
    from sprinkler.env.plog.plog_analysis import main_fault_matcher
    FAULT_CSV = os.path.join(current_dir, "plog_fault_library.csv")
    NO_ANCHOR_SCAN_CONTEXT = (5, 5) # (向前行数, 向后行数)
    main_fault_matcher(log_path, FAULT_CSV, NO_ANCHOR_SCAN_CONTEXT)
    print("[Info][End] Finish analysis ERROR in plog.")

def log_save_local():
    command = ['bash', os.path.dirname(_RUN_PATH)+ '/sprinkler/env/plog/pod_collect_logs.sh']
    process = subprocess.Popen(command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()

def log_clean_local():
    command = ['bash', os.path.dirname(_RUN_PATH)+ '/sprinkler/env/plog/pod_clean_logs.sh']
    process = subprocess.Popen(command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()

def check_log_config():
    command = ['bash', os.path.dirname(_RUN_PATH)+ '/sprinkler/env/plog/check_log.sh']
    process = subprocess.Popen(command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()

def log_save_qemu():
    command = ['bash', os.path.dirname(_RUN_PATH)+ '/sprinkler/env/plog/qemu_gather_logs.sh']
    process = subprocess.Popen(command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()

def log_clean_qemu():
    command = ['bash', os.path.dirname(_RUN_PATH)+ '/sprinkler/env/plog/qemu_clean_logs.sh']
    process = subprocess.Popen(command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()

def reinsert_ipourma_ko():
    command = ['bash', os.path.dirname(_RUN_PATH)+ '/sprinkler/env/plog/reinsert_ipourma_ko.sh']
    process = subprocess.Popen(command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()

def check_chip_delay():
    command = ['bash', os.path.dirname(_RUN_PATH)+ '/sprinkler/env/plog/check_chip_delay.sh']
    process = subprocess.Popen(command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()

def collect_logs_entry():
    from sprinkler.plog.log_collector import LogCollector
    collector = LogCollector(args=get_args())
    result = collector.collect()
    return 0 if result.failed == 0 else 1

def plog_exec():
    args = get_args()
    if args.plog_error:
        plog_error_print(args)
    if args.plog_error_analysis:
        plog_error_analysis(args)
    if args.log_save:
        log_save_local()
    if args.log_clean:
        log_clean_local()
    if args.check_log:
        check_log_config()
    if args.log_qemu_save:
        log_save_qemu()
    if args.log_qemu_clean:
        log_clean_qemu()
    if args.reinsert_ipourma_ko:
        reinsert_ipourma_ko()
    if args.check_chip_delay:
        check_chip_delay()
    if getattr(args, "collect_logs", False):
        collect_logs_entry()
    run_from_args(args)
