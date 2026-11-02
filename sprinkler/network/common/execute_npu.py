import subprocess
import sys
import os
import argparse
from pathlib import Path

# 配置常量
NPU_USER = "root"
ROUTE_CONF = "/lib/route.conf"


def get_valid_path(prompt="请输入路径: "):
    """提示用户输入有效路径，直到输入正确为止"""
    while True:
        path = input(f"🔍 {prompt}").strip()
        if os.path.exists(path):
            return path
        print(f"❌ 错误: 路径 '{path}' 不存在，请重新输入。") 

def get_command_from_file(file_path):
    try:
        with open(file_path, 'r') as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print(f"❌ 错误：文件不存在: {file_path}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ 读取文件失败: {str(e)}", file=sys.stderr)
        sys.exit(1)


execute_file = get_valid_path("请输入执行文件路径 (如 execute.txt)\n")

# 从命令行参数获取文件路径
COMMANDS = get_command_from_file(execute_file)

def get_npu_ip():
    """从配置文件获取NPU IP地址（与Bash脚本逻辑完全一致）"""
    if not os.path.isfile(ROUTE_CONF):
        print(f"ERROR: 配置文件不存在: {ROUTE_CONF}", file=sys.stderr)
        return None
    
    # 与Bash脚本相同的IP解析逻辑
    try:
        ip_str = subprocess.check_output(
            f"cat /lib/route.conf | grep remote | awk '{{\n        len=length($0)\n        if(len < 8) next\n        s=substr($0, len-7, 8)\n        part1=substr(s,1,2)\n        part2=substr(s,3,2)\n        part3=substr(s,5,2)\n        part4=substr(s,7,2)\n        cmd=\"printf \\\"%d.%d.%d.%d\\\\n\\\" 0x\" part1 \" 0x\" part2 \" 0x\" part3 \" 0x\" part4\n        system(cmd)\n    }}'",
            shell=True,
            text=True
        ).strip()
        return ip_str
    except subprocess.CalledProcessError:
        print("ERROR: 无法获取NPU IP地址", file=sys.stderr)
        return None


def execute_on_npu(npu_ip):
    """在NPU上执行命令"""
    print(f"正在NPU侧执行命令: {npu_ip}")
    i = 1
    # 与Bash脚本相同的SSH命令
    cmd = f"sshpass ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 -t {NPU_USER}@{npu_ip} <<EOF\n"
    for command in COMMANDS:
        cmd += f"echo '第{i}条命令：{command} 的输出为：'\n"
        cmd += f"{command} 2>&1 \n"
        i += 1
    cmd += "echo 'SUCCESS: 命令已全部执行'\nEOF"
    
    try:
        result = subprocess.check_output(cmd, shell=True, text=True)
        print(f"==== {result} ==== ")
        return result
    except subprocess.CalledProcessError as e:
        print(f"ERROR: NPU执行命令失败: {e}", file=sys.stderr)
        return None

def main():

    # 获取NPU IP
    npu_ips = sys.argv[1:]
    if not npu_ips:
        print("请手动输入NPU IP地址: ", end="", file=sys.stderr)
        npu_ip = input().strip()
    
    if not npu_ips:
        print("ERROR: 未提供NPU IP地址", file=sys.stderr)
        sys.exit(1)
    
    # 执行查询
    results = []
    for npu_ip in npu_ips:
        results.append(execute_on_npu(npu_ip))
    if not results:
        sys.exit(1)
    
    # 保存结果到文件
    output_file = f"execute_on_npu_results_{os.path.basename(__file__).split('.')[0]}_{os.getenv('DATE', 'now')}.txt"
    with open(output_file, "w") as f:
        f.write(f"=== Results for {npu_ip} ===\n")
        for res in results:
            f.write(res)
    
    print(f"\n 执行完成，结果已保存至: {output_file}")

if __name__ == "__main__":
    main()
