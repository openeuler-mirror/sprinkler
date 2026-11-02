#!/usr/bin/env python3
import subprocess
import re
import sys
import random
from datetime import datetime

# ============ ANSI 颜色定义 ============
COLOR_OK    = "\033[32m"    # 绿色
COLOR_WARN  = "\033[33m"    # 黄色
COLOR_FAIL  = "\033[31m"    # 红色
COLOR_INFO  = "\033[36m"    # 青色
COLOR_OFF   = "\033[0m"     # 重置

CHECK_ID = "NET_CHECK"


def log(level: str, msg: str):
    """按照 bash log 格式输出带颜色的日志"""
    color_map = {
        "OK":   COLOR_OK,
        "WARN": COLOR_WARN,
        "FAIL": COLOR_FAIL,
        "INFO": COLOR_INFO,
    }
    color = color_map.get(level, COLOR_OFF)
    now = datetime.now().strftime("%F %T")
    # 与 bash 保持一致: 时间 | 等级 | CHECK_ID | 消息
    print(f"{now} | {color}{level:<4}{COLOR_OFF} | {CHECK_ID} | {msg}")


# ============ MAC 随机生成逻辑 ============
def generate_random_mac():
    """生成符合 IEEE 802 标准的本地管理单播 MAC 地址"""
    first_byte_choices = [f"{b:02X}" for b in range(0x00, 0x100)
                          if (b & 0x01 == 0x00) and (b & 0x02 == 0x02)]
    first = random.choice(first_byte_choices)
    rest = [f"{random.randint(0x00, 0xFF):02X}" for _ in range(5)]
    return ":".join([first] + rest)


# ============ 主流程 ============
def main():
    # --- 1. 解析命令行参数 ---
    if len(sys.argv) < 2:
        log("FAIL", "缺少参数，用法: python check_eth.py <服务器IP>")
        sys.exit(1)

    target_ip = sys.argv[1]
    if not re.match(r'^\d{1,3}(\.\d{1,3}){3}$', target_ip):
        log("FAIL", f"参数 '{target_ip}' 不是合法的 IP 地址")
        sys.exit(1)

    # --- 2. 执行 ifconfig 并按空行分段 ---
    result = subprocess.run(["ifconfig"], capture_output=True, text=True)
    output = result.stdout
    blocks = re.split(r'\n\s*\n', output)

    matched = []        # ethX 正常列表
    data_errors = []    # data... 异常列表

    for block in blocks:
        lines = block.strip().splitlines()
        if not lines:
            continue

        header_match = re.match(r'^(eth[0-9])\b', lines[0])
        data_match = re.match(r'^(data\S*):', lines[0])

        # ---- 匹配异常网卡 data... ----
        if data_match:
            iface = data_match.group(1)
            content = "\n      ".join(lines)
            data_errors.append((iface, content))
            continue

        # ---- 匹配正常网卡 ethX ----
        if not header_match:
            continue

        iface = header_match.group(1)
        ip = None
        mac = None

        for line in lines:
            m_ip = re.search(r'inet\s+(\d{1,3}(?:\.\d{1,3}){3})', line)
            if m_ip:
                ip = m_ip.group(1)

            m_mac = re.search(r'ether\s+([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})', line)
            if m_mac:
                mac = m_mac.group(1).lower()

        if ip and mac:
            matched.append((iface, ip, mac))

    # --- 3. 打印所有 ethX 匹配项 ---
    for iface, ip, mac in matched:
        log("OK", f"{iface} 的 IP 为 {ip}，MAC 地址为 {mac}")

    if not matched:
        log("OK", "共检测到 0 个网卡。")
    else:
        log("OK", f"共检测到 {len(matched)} 个网卡。")

    # --- 4. 检查异常网卡 data... [WARN] ---
    if data_errors:
        log("WARN", f"检测到 {len(data_errors)} 个异常网卡（data 开头），这是错误的网卡设置：")
        for iface, content in data_errors:
            print(f"      异常网卡: {iface}")
            print(f"        {content}")
            log("WARN", f"修复命令: ifdown {iface}")
        print()

    # --- 5. 检查目标 IP 是否重复绑定 [WARN] ---
    ip_cards = [(iface, ip, mac) for iface, ip, mac in matched if ip == target_ip]
    if len(ip_cards) >= 2:
        iface_list = ", ".join([item[0] for item in ip_cards])
        log("WARN", f"当前网卡不正常，存在多个网卡 ({iface_list}) 绑定同一 IP {target_ip}，请检查网络配置。")
    elif len(ip_cards) == 1:
        log("OK", f"服务器 IP {target_ip} 仅绑定在 {ip_cards[0][0]} 上。")
    else:
        log("INFO", f"未找到绑定 IP {target_ip} 的网卡。")

    # --- 6. 检查 MAC 冲突 08:08:08 开头 [WARN] ---
    conflict_cards = [(iface, ip, mac) for iface, ip, mac in matched
                      if mac.startswith("08:08:08")]

    if conflict_cards:
        log("WARN", "检测到 MAC 地址冲突（以 08:08:08 开头），需要重新设置 MAC 地址！")
        print()
        for iface, ip, mac in conflict_cards:
            new_mac = generate_random_mac()
            print(f"      --- {iface} 修复命令 ---")
            print(f"        ifdown {iface}")
            print(f"        ifconfig {iface} hw ether {new_mac}")
            print(f"        ifconfig {iface} {ip} netmask 255.255.252.0")
            print(f"        route add default gw 141.61.48.1")
            print()


if __name__ == "__main__":
    main()

