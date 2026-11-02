#!/usr/bin/env python3
"""BMC上下电工具（手动凭据版）

用户手动输入 BMC IP、用户名、密码，通过 SSH 直连后执行上电、下电、查看电源状态操作。
"""

import getpass
from datetime import datetime

import paramiko

CHECK_ID = "BMC_MANUAL"

BMC_POWER_ON_CMD = "source /etc/profile; echo y | /opt/bmc/skynet/lua /opt/bmc/apps/cli/service/ipmcset.lua -d powerstate -v 1"
BMC_POWER_OFF_CMD = "source /etc/profile; echo y | /opt/bmc/skynet/lua /opt/bmc/apps/cli/service/ipmcset.lua -d powerstate -v 0"
BMC_POWER_STATUS_CMD = "source /etc/profile; /opt/bmc/skynet/lua /opt/bmc/apps/cli/service/ipmcget.lua -d powerstate"


def log(level: str, check_id: str, msg: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] [{check_id}] {msg}")


class SshConnection:
    """SSH连接，支持远程执行命令"""
    def __init__(self, host: str, username: str, password: str, timeout: int = 20):
        self.host = host
        self.username = username
        self.password = password
        self.timeout = timeout
        self.client = None

    def connect(self) -> bool:
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.WarningPolicy())
            self.client.connect(
                hostname=self.host,
                port=22,
                username=self.username,
                password=self.password,
                timeout=self.timeout,
                banner_timeout=self.timeout,
                auth_timeout=self.timeout
            )
            log("INFO", CHECK_ID, f"SSH连接 {self.host} 成功")
            return True
        except paramiko.AuthenticationException:
            log("FAIL", CHECK_ID, f"SSH {self.host} 认证失败: 用户名或密码错误")
        except Exception as e:
            log("FAIL", CHECK_ID, f"SSH {self.host} 连接失败: {type(e).__name__}: {e}")
        return False

    def exec_command(self, command: str, timeout: int = 60) -> tuple:
        if not self.client:
            return (-1, "", "Not connected")
        try:
            stdin, stdout, stderr = self.client.exec_command(command)
            stdout.channel.settimeout(timeout)
            stderr.channel.settimeout(timeout)
            stdout_content = stdout.read().decode('utf-8', errors='ignore')
            stderr_content = stderr.read().decode('utf-8', errors='ignore')
            exit_code = stdout.channel.recv_exit_status()
            return (exit_code, stdout_content, stderr_content)
        except Exception as e:
            return (-1, "", str(e))

    def close(self):
        if self.client:
            self.client.close()
            self.client = None


def run_cmd(bmc_ip: str, username: str, password: str, cmd: str) -> tuple:
    conn = SshConnection(bmc_ip, username, password)
    if not conn.connect():
        return (255, "", f"连接 BMC {bmc_ip} 失败")
    rc, stdout, stderr = conn.exec_command(cmd)
    conn.close()
    return (rc, stdout, stderr)


def parse_power_state(output: str):
    try:
        for line in output.splitlines():
            if "power state" in line.lower():
                parts = line.split(":", 1)
                if len(parts) == 2:
                    return parts[1].strip()
    except Exception:
        pass
    return None


def exec_bmc_power(bmc_ip: str, username: str, password: str, action: str):
    action_desc = {"on": "上电", "off": "下电", "status": "查看电源状态"}
    cmd_map = {"on": BMC_POWER_ON_CMD, "off": BMC_POWER_OFF_CMD, "status": BMC_POWER_STATUS_CMD}
    log("INFO", CHECK_ID, f"BMC {bmc_ip} 执行 {action_desc[action]}...")
    cmd = cmd_map[action]

    exit_code, stdout, stderr = run_cmd(bmc_ip, username, password, cmd)
    output = (stdout + stderr).strip()

    if action != "status":
        if exit_code == 0:
            if "success" in output.lower():
                log("INFO", CHECK_ID, f"BMC {bmc_ip} {action_desc[action]}成功")
            else:
                log("WARN", CHECK_ID, f"BMC {bmc_ip} {action_desc[action]}命令执行成功(exit=0)，但输出未含success字样")
        else:
            log("ERROR", CHECK_ID, f"BMC {bmc_ip} {action_desc[action]}执行失败 (exit={exit_code}): {output}")
        if output:
            print(output)
        return

    if exit_code == 0:
        state = parse_power_state(output)
        if state:
            desc = ""
            if state.lower() == "on":
                desc = " (上电状态)"
            elif state.lower() == "off":
                desc = " (下电状态)"
            log("INFO", CHECK_ID, f"BMC {bmc_ip} 电源状态: {state}{desc}")
        else:
            log("WARN", CHECK_ID, f"BMC {bmc_ip} 无法解析电源状态，原始输出: {output}")
            log("INFO", CHECK_ID, f"BMC {bmc_ip} 原始输出:")
            print(output)
    else:
        log("ERROR", CHECK_ID, f"BMC {bmc_ip} 查看电源状态失败 (exit={exit_code}): {output}")


def main():
    print("\n" + "=" * 60)
    print("BMC上下电工具")
    print("=" * 60)

    print("\n--- 输入待操作BMC信息 ---")
    bmc_ip = input("  BMC IP: ").strip()
    bmc_user = input("  BMC SSH用户名: ").strip()
    bmc_pw = getpass.getpass("  BMC SSH密码: ")
    if not bmc_ip or not bmc_user or not bmc_pw:
        log("FAIL", CHECK_ID, "BMC IP、用户名、密码均不能为空")
        return

    print("\n" + "=" * 60)
    print("请选择要执行的操作:")
    print("  1. 上电 (on)")
    print("  2. 下电 (off)")
    print("  3. 查看电源状态 (status)")
    print("  4. 退出 (q)")
    print("=" * 60)

    action_map = {"1": "on", "2": "off", "3": "status", "4": "q"}
    while True:
        choice = input("\n请输入操作编号 (1-4): ").strip().lower()
        if choice in ("q", "4", "quit", "exit"):
            log("INFO", CHECK_ID, "用户退出")
            break
        action = action_map.get(choice)
        if not action:
            print("无效的编号，请重新选择")
            continue
        exec_bmc_power(bmc_ip, bmc_user, bmc_pw, action)
        another = input("\n是否继续操作该BMC? (y继续/任意键退出): ").strip().lower()
        if another not in ("y", "yes", ""):
            break


def power_control_exec(args=None):
    main()

if __name__ == "__main__":
    main()
