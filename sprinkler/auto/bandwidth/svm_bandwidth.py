import os
import subprocess
import socket

from sprinkler.connect.ssh.os import SSHOSConnector
from sprinkler.connect.targets import TargetContext, NodeIpSpec
from multiprocessing import Process, Queue


def _is_cross_board(args):
    """跨框打流判定: -node 为 IP;user;pwd 格式直接跨框; 结构化格式需 -xlsx."""
    node_text = str(getattr(args, "node", "") or "").strip()
    if not node_text:
        return False
    if NodeIpSpec.is_ip_spec(node_text):
        return True
    return bool(getattr(args, "xlsx", None))


local_ip = socket.gethostbyname(socket.gethostname())


def remote_svm_bandwidth(os_conn, target, command, result_queue, machine_label):

    dst_client = os_conn.connect_client(target)

    result = os_conn.run_command(dst_client, target, command)
    if result_queue is not None:
        stdout_text = result.get("stdout", "") or ""
        pass_cnt = stdout_text.count("St pass")
        result_queue.put((machine_label, pass_cnt, stdout_text))



def svm_bandwidth(args):

    # 本地检查 svm_path 和 cann_path 文件是否存在
    if not os.path.isfile(args.svm_path):
        print(f"错误: svm_path 文件不存在 -> {args.svm_path}")
        return

    if not os.path.isfile(args.cann_path):
        print(f"错误: cann source 文件不存在 -> {args.cann_path}")
        return

    # 确定是否是跨框打流
    if _is_cross_board(args):
        targets = TargetContext(args).get_targets("cpu_os")

        # 远程检查所有目的机上 svm_path 和 cann_path 文件是否存在
        print(f"\n{'='*60}")
        print(f"检查远程机器文件是否存在...")
        print(f"{'='*60}")
        os_conn = SSHOSConnector()
        all_remote_ok = True
        for target in targets:
            target_ip = target.host
            client = os_conn.connect_client(target)
            for check_path, label in [(args.svm_path, "svm_path"), (args.cann_path, "cann_path")]:
                check_cmd = f"test -f {check_path} && echo 'EXISTS' || echo 'NOT_EXISTS'"
                check_result = os_conn.run_command(client, target, check_cmd)
                check_stdout = check_result.get("stdout", "") or ""
                if "NOT_EXISTS" in check_stdout:
                    print(f"错误: 目的侧 {target_ip} 上 {label} 文件不存在 -> {check_path}")
                    all_remote_ok = False
                else:
                    print(f"OK: 目的侧 {target_ip} 上 {label} 存在")
        if not all_remote_ok:
            print("远程文件检查未通过，终止执行")
            return
        print(f"{'='*60}\n")

        # 源侧执行
        src_command = f"""
        source {args.cann_path} &&
        {args.svm_path} 9999 9 0 {args.cycles} st_svm_p2p_cs_test_server {args.port} {args.device_num} {args.ipc_size} {args.incrememt_size} {args.op_size}
        """

        print(f"\n{'='*60}")
        print(f"源侧 (本机 {local_ip}) 执行中...")
        print(f"{'='*60}")
        process = subprocess.Popen(src_command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            encoding='utf-8',
                            errors='replace',
                            bufsize=1,
                            shell=True)
        src_stdout_lines = []
        for line in process.stdout:
            print(line, end='')
            src_stdout_lines.append(line)
        process.wait()

        src_pass_count = sum(line.count("St pass") for line in src_stdout_lines)
        print(f"\n源侧 St pass 出现次数: {src_pass_count}")

        # 目的侧批量执行
        print(f"\n{'='*60}")
        print(f"目的侧批量执行中...")
        print(f"{'='*60}")
        os_conn = SSHOSConnector()
        process_list = []
        result_queue = Queue()

        for target in targets:
            target_ip = target.host
            dst_command = f"""
            source {args.cann_path} &&
            for i in {{0..{args.device_num-1}}}
            do
                for j in {{0..{args.device_num-1}}}
                do
                    if [ $i -ne $j ]; then
                        echo "源端ip={local_ip}, 源端devid=$i, 目的端devid=$j，size={args.ipc_size}，incrememt_size={args.incrememt_size}，op_size={args.op_size} "
                        {args.svm_path} 9999 3 $j 1 st_svm_p2p_cs_test_client {args.port} $i {args.ipc_size} {args.incrememt_size} {args.op_size} <<< {local_ip} &
                        sleep 5;
                    fi
                done
            done
            wait
            """
            p = Process(target=remote_svm_bandwidth,
                        args=(os_conn, target, dst_command, result_queue, target_ip))
            p.start()
            process_list.append(p)

        for p in process_list:
            p.join()

        # 收集目的侧结果
        dst_results = []
        while not result_queue.empty():
            dst_results.append(result_queue.get())

        # 汇总输出
        print(f"\n{'='*60}")
        print(f"汇总结果")
        print(f"{'='*60}")
        print(f"{'源侧':<20} | {'本机':<20} | St pass: {src_pass_count}")
        print(f"{'-'*60}")
        total_dst_pass = 0
        for ip, pass_cnt, _ in dst_results:
            print(f"{'目的侧':<20} | {ip:<20} | St pass: {pass_cnt}")
            total_dst_pass += pass_cnt
        print(f"{'='*60}")
        print(f"目的侧 St pass 合计: {total_dst_pass}")
        print(f"{'='*60}")
    else:
        command = f"""
        source {args.cann_path} &&
        for i in {{0..{args.device_num -1}}}
        do
            for j in {{0..{args.device_num -1}}}
            do
                if [ $i -ne $j ]; then
                    echo "执行命令: {args.svm_path} 9999 9 0 {args.cycles} st_svm_p2p_test_009 $i $j {args.dst_open_num} {args.ipc_size} {args.pg_type}"
                    {args.svm_path} 9999 9 0 {args.cycles} st_svm_p2p_test_009 $i $j {args.dst_open_num} {args.ipc_size} {args.pg_type} &
                    sleep 5;
                fi
            done
        done
        wait
        """
        print(f"commnd : \n{command}", end="")
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
            shell=True,
        )
        stdout_lines = []
        for line in process.stdout:
            print(line, end="")
            stdout_lines.append(line)
        process.wait()

        # 汇总 St pass 出现次数
        pass_count = 0
        for line in stdout_lines:
            pass_count += line.count("St pass")
        print(f"\n{'='*60}")
        print(f"St pass 应该出现次数为: {args.device_num * (args.device_num -1)} 实际出现次数: {pass_count}")
        print(f"{'='*60}")