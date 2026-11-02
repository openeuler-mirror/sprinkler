"""
防火墙检测与关闭（融合版）
功能: 检测 firewalld/iptables 状态，如果检测到未关闭则提示用户选择是否关闭
"""

import re
import subprocess
import sys
from dataclasses import dataclass, field

from sprinkler.base.log import RunLog

# ANSI 转义序列匹配模式
_ANSI_ESCAPE = re.compile(r'\033\[[0-9;]*m')


# ============================================================
# 防火墙检测
# ============================================================


@dataclass
class FirewallServiceStatus:
    """单个防火墙服务的状态"""
    name: str
    enabled: bool
    enabled_legacy: str
    detail: str = ""


@dataclass
class FirewallCheckResult:
    """防火墙检测结果"""
    services: list[FirewallServiceStatus] = field(default_factory=list)

    @property
    def all_inactive(self) -> bool:
        return all(not s.enabled for s in self.services)

    @property
    def any_active(self) -> bool:
        return any(s.enabled for s in self.services)


def _check_service_status(service_name: str) -> FirewallServiceStatus | None:
    """检查单个 systemd 服务的运行状态"""
    try:
        proc = subprocess.run(
            ["systemctl", "status", service_name],
            capture_output=True, text=True, timeout=15,
        )
        for line in proc.stdout.splitlines():
            if "Active:" in line:
                parts = line.strip().split()
                if len(parts) >= 2:
                    status = parts[1].strip().rstrip("()")
                    return FirewallServiceStatus(
                        name=service_name,
                        enabled=(status == "active"),
                        enabled_legacy=status,
                        detail=line.strip(),
                    )
        return FirewallServiceStatus(
            name=service_name,
            enabled=(proc.returncode == 0),
            enabled_legacy="active" if proc.returncode == 0 else "inactive",
            detail=f"exit code: {proc.returncode}",
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return FirewallServiceStatus(name=service_name, enabled=False, enabled_legacy="unknown", detail="timeout")
    except Exception as exc:
        return FirewallServiceStatus(name=service_name, enabled=False, enabled_legacy="unknown", detail=str(exc))


def check_firewall() -> FirewallCheckResult:
    """检测所有防火墙服务状态"""
    result = FirewallCheckResult()
    for svc in ["firewalld", "iptables.service"]:
        status = _check_service_status(svc)
        if status is not None:
            result.services.append(status)
    return result


def format_firewall_result(result: FirewallCheckResult) -> list[str]:
    """格式化防火墙检测结果"""
    lines = []
    for svc in result.services:
        name_cn = "firewalld" if svc.name == "firewalld" else "iptables"
        status_cn = "运行中" if svc.enabled else "已关闭"
        lines.append(f" {name_cn} {status_cn}")

    if result.all_inactive:
        lines.append("=" * 40)
        lines.append("\033[32m防火墙已全部关闭\033[0m")
    else:
        lines.append("\033[31m防火墙未关闭！\033[0m")
    return lines


@dataclass
class FirewallCommand:
    """防火墙命令定义"""
    cmd: list[str]
    description: str


@dataclass
class FirewallResult:
    """防火墙操作结果"""
    success_cmds: list[str] = field(default_factory=list)
    failed_cmds: list[str] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return len(self.failed_cmds) == 0


FIREWALL_COMMANDS: list[FirewallCommand] = [
    FirewallCommand(cmd=["systemctl", "stop", "firewalld"],
                    description="停止firewalld服务"),
    FirewallCommand(cmd=["systemctl", "disable", "firewalld.service"],
                    description="禁用firewalld服务开机自启"),
    FirewallCommand(cmd=["systemctl", "stop", "iptables.service"],
                    description="停止iptables服务"),
]


class FirewallManager:
    """防火墙管理器"""

    def __init__(self, log: RunLog):
        self.log = log

    def _check_and_report(self) -> bool:
        """检测防火墙当前状态并打印状态信息

        返回:
            True 表示有服务处于活跃状态, False 表示全部已关闭
        """
        result = check_firewall()
        for line in format_firewall_result(result):
            self.log.info(_ANSI_ESCAPE.sub("", line))
        return result.any_active

    def stop_firewall(self) -> FirewallResult:
        """
        执行关闭防火墙操作
        返回: 操作结果
        """
        self.log.info("=" * 40)
        self.log.info("开始关闭防火墙")

        result = FirewallResult()

        for fw_cmd in FIREWALL_COMMANDS:
            self.log.info(f"执行: {fw_cmd.description}")
            self.log.info(f"命令: {' '.join(fw_cmd.cmd)}")

            if self._run_command(fw_cmd.cmd):
                result.success_cmds.append(fw_cmd.description)
            else:
                result.failed_cmds.append(fw_cmd.description)

        self.log.info("=" * 40)
        # 执行完毕后再次检测确认
        self.log.info("关闭完成，再次检测防火墙状态...")
        confirm = check_firewall()
        for line in format_firewall_result(confirm):
            self.log.info(_ANSI_ESCAPE.sub("", line))

        if confirm.all_inactive:
            self.log.ok("防火墙已全部关闭")
        elif result.all_passed:
            self.log.fail("命令已执行但防火墙仍未关闭，请手动检查")

        return result

    def _run_command(self, cmd: list[str]) -> bool:
        """
        执行系统命令
        参数:
            cmd: 命令及参数列表
        返回:
            是否执行成功
        """
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode == 0:
                self.log.ok(f"执行成功: {' '.join(cmd)}")
                return True
            else:
                error_msg = proc.stderr.strip() or proc.stdout.strip()
                self.log.fail(f"执行失败: {' '.join(cmd)} (rc={proc.returncode})")
                if error_msg:
                    self.log.fail(f"错误信息: {error_msg}")
                return False
        except subprocess.TimeoutExpired:
            self.log.fail(f"命令超时: {' '.join(cmd)}")
            return False
        except FileNotFoundError:
            self.log.fail(f"命令不存在: {cmd[0]}")
            return False


def _prompt_and_stop(log: RunLog) -> FirewallResult:
    """检测防火墙状态，检测到未关闭时交互式询问是否关闭

    返回:
        FirewallResult: 关闭操作结果
    """
    log.info("=" * 40)
    log.info(" 检查防火墙状态")

    # 检测防火墙状态
    result = check_firewall()
    for line in format_firewall_result(result):
        log.info(_ANSI_ESCAPE.sub("", line))
    has_active = result.any_active

    if not has_active:
        return FirewallResult()

    # 交互式询问
    print("检测到防火墙未关闭，是否立即关闭？[Y/n]: ", end="", flush=True)
    try:
        answer = sys.stdin.readline().strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""

    if answer in ("", "y", "yes"):
        log.info("用户选择关闭防火墙")
        manager = FirewallManager(log)
        return manager.stop_firewall()
    else:
        log.info("用户选择不关闭防火墙")
        return FirewallResult(failed_cmds=["用户取消操作"])


def stop_firewall_check(args) -> int:
    """
    防火墙检测与关闭入口（自动模式：检测到未关闭则交互式询问）
    参数:
        args: 命令行参数对象
    返回:
        退出码 (0表示成功, 1表示失败)
    """
    log = RunLog(args, "firewall")
    print(f"日志目录: {log.path}")

    result = _prompt_and_stop(log)
    return 0 if result.all_passed else 1