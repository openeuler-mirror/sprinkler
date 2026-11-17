import os
import subprocess
import time

from sprinkler.arguments import get_args
from sprinkler.utils.utils import _RUN_PATH, find_all_shell, package_path


def run_from_args(args):
    roads = {}
    if args.plog or args.all:
        roads = str(package_path() / "plog" / "check_log.sh")

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
    command = ['bash', str(package_path() / "plog" / "plog_search.sh")] + script_args
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
        command = ['bash', str(package_path() / "plog" / "plog_search.sh")] + script_args
        process = subprocess.Popen(command,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                text=True,
                                bufsize=1)
        for line in process.stdout:
            print(line, end='')
        process.wait()

    # plog报错匹配
    from sprinkler.plog.plog_analysis import main_fault_matcher
    from sprinkler.plog.fault_matcher import DEFAULT_LIBRARY_PATH
    FAULT_CSV = DEFAULT_LIBRARY_PATH
    NO_ANCHOR_SCAN_CONTEXT = (5, 5) # (向前行数, 向后行数)
    main_fault_matcher(log_path, FAULT_CSV, NO_ANCHOR_SCAN_CONTEXT)
    print("[Info][End] Finish analysis ERROR in plog.")

def log_save_local():
    command = ['bash', str(package_path() / "plog" / "pod_collect_logs.sh")]
    process = subprocess.Popen(command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()

def log_clean_local():
    command = ['bash', str(package_path() / "plog" / "pod_clean_logs.sh")]
    process = subprocess.Popen(command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()

def check_log_config():
    command = ['bash', str(package_path() / "plog" / "check_log.sh")]
    process = subprocess.Popen(command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()

def log_save_qemu():
    command = ['bash', str(package_path() / "plog" / "qemu_gather_logs.sh")]
    process = subprocess.Popen(command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()

def log_clean_qemu():
    command = ['bash', str(package_path() / "plog" / "qemu_clean_logs.sh")]
    process = subprocess.Popen(command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()

def reinsert_ipourma_ko():
    command = ['bash', str(package_path() / "plog" / "reinsert_ipourma_ko.sh")]
    process = subprocess.Popen(command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1)
    for line in process.stdout:
        print(line, end='')
    process.wait()

def check_chip_delay():
    command = ['bash', str(package_path() / "plog" / "check_chip_delay.sh")]
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

def fault_match_entry(args):
    """-fault-match 入口：校验参数，构造 FaultMatcher 并输出命中清单。"""
    from sprinkler.plog.fault_matcher import (
        FaultMatcher, DEFAULT_LIBRARY_PATH, DEFAULT_RESULT_DIRNAME, OUTPUT_FORMATS)
    log_dir = getattr(args, "log_dir", None)
    if not log_dir:
        print("[Error] -fault-match 需要配合 -log-dir <日志目录> 使用")
        return 1
    if not os.path.isdir(log_dir):
        print("[Error] 日志目录不存在或不是目录: {}".format(log_dir))
        return 1
    output_format = getattr(args, "output_format", "table") or "table"
    if output_format not in OUTPUT_FORMATS:
        print("[Error] 非法的输出格式: {}，可选: {}".format(output_format, "/".join(OUTPUT_FORMATS)))
        return 1
    library = getattr(args, "fault_library", None) or DEFAULT_LIBRARY_PATH
    print("[Info][Start] Fault match. log dir: {}, library: {}".format(log_dir, library))
    try:
        matcher = FaultMatcher(library_path=library, output_format=output_format)
    except (FileNotFoundError, ValueError) as e:
        print("[Error] {}".format(e))
        return 1
    hits = matcher.match_directory(log_dir)
    result_text = matcher.format_results(hits)
    print(result_text)
    output_dir = getattr(args, "output_dir", None) or os.path.join(log_dir, DEFAULT_RESULT_DIRNAME)
    ext = {"table": "txt", "json": "json", "csv": "csv"}[output_format]
    result_file = os.path.join(
        output_dir, "fault_match_{}.{}".format(time.strftime("%Y%m%d_%H%M%S"), ext))
    try:
        if not os.path.isdir(output_dir):
            os.makedirs(output_dir)
        with open(result_file, "w", encoding="utf-8") as f:
            f.write(result_text + "\n")
        print("[Info] 结果输出目录: {}".format(os.path.abspath(output_dir)))
        print("[Info] 结果文件: {}".format(os.path.abspath(result_file)))
    except OSError as e:
        print("[Warn] 结果文件写入失败（结果已在上方打印）: {}: {}".format(result_file, e))
    print("[Info][End] Finish fault match.")
    return 0

def plog_exec():
    args = get_args()
    if getattr(args, "fault_match", False):
        fault_match_entry(args)
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
