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

def get_registers_from_file(file_path):
    """从指定文件读取寄存器地址列表"""
    try:
        with open(file_path, 'r') as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print(f"❌ 错误：文件不存在: {file_path}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ 读取文件失败: {str(e)}", file=sys.stderr)
        sys.exit(1)


print("Warn 此功能是查询NPU端寄存器值，谨慎使用，确定查询值正确 ~ ~")
register_file = get_valid_path("请输入寄存器文件路径 (如 devm.txt)\n")

# 从命令行参数获取文件路径
REGISTERS = get_registers_from_file(register_file)

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


def validate_hex_string(hex_str):
    if not isinstance(hex_str, str):
        raise ValueError("输入必须是字符串类型")
    """验证NPU寄存器地址格式规范（必须是0x248/0x2c8+8位字符，总长度13）"""
    # 1. 长度校验
    if len(hex_str) != 13:
        raise ValueError(
            f"寄存器地址长度错误: '{hex_str}' (应为13位字符，当前长度: {len(hex_str)})"
            f"\n正确格式示例: '0x24803031794' (0x248+8位字符)"
        )

    # 2. 基础前缀校验
    if not hex_str.startswith("0x"):
        raise ValueError(
            f"无效的十六进制字符串: '{hex_str}'. 必须以'0x'开头"
        )

    # 3. 有效前缀校验 (0x248或0x2c8)
    if not (hex_str.startswith("0x248") or hex_str.startswith("0x2c8")):
        raise ValueError(
            f"无效的寄存器前缀: '{hex_str[:5]}'. 必须是'0x248'或'0x2c8'"
            f"\n当前NPU支持型号: 0x248 (A5芯片) / 0x2c8 (A5+芯片)"
        )

    # 4. 字符校验 (确保后续8位是十六进制字符)
    if not all(c in '0123456789abcdefABCDEF' for c in hex_str[5:]):
        raise ValueError(
            f"寄存器地址包含非法字符: '{hex_str}' (第6-13位应为十六进制字符)"
        )

    return

def query_registers(npu_ip):
    """在NPU上执行devmem命令查询寄存器（与Bash脚本完全一致）"""
    print(f"正在查询NPU: {npu_ip}")
    
    # 与Bash脚本相同的SSH命令
    cmd = f"sshpass ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 {NPU_USER}@{npu_ip} <<EOF\n"
    for reg in REGISTERS:
        validate_hex_string(reg.split()[1])
        cmd += f"echo '{reg}'; {reg} | tail -n 1\n"
        cmd += f"echo '{reg.split()[0]} {reg.split()[1]}'; {reg.split()[0]} {reg.split()[1]} | tail -n 1\n"
    # cmd += "echo 'SUCCESS: 修改成功'\nEOF"
    
    try:
        result = subprocess.check_output(cmd, shell=True, text=True)
        print(f"==== {result} ==== ")
        return result
    except subprocess.CalledProcessError as e:
        print(f"ERROR: NPU寄存器修改失败: {e}", file=sys.stderr)
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
        results.append(query_registers(npu_ip))
    if not results:
        sys.exit(1)
    
    # 保存结果到文件
    output_file = f"write_register_results_{os.path.basename(__file__).split('.')[0]}_{os.getenv('DATE', 'now')}.txt"
    with open(output_file, "w") as f:
        f.write(f"=== Results for {npu_ip} ===\n")
        for res in results:
            f.write(res)
    
    print(f"\n✅ 修改完成，结果已保存至: {output_file}")

if __name__ == "__main__":
    main()
