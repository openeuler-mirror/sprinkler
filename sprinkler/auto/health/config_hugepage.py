"""
配置CPU大页内存功能
功能: 开启CPU大页内存(HugePages), 不配置寄存器
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from sprinkler.base.log import RunLog


@dataclass
class HugePageConfig:
    """大页内存配置参数"""
    hugepages_count: int = 8192
    nr_hugepages_path: str = "/proc/sys/vm/nr_hugepages"
    meminfo_path: str = "/proc/meminfo"


@dataclass
class HugePageResult:
    """大页内存配置结果"""
    meminfo_before: str = ""
    meminfo_after: str = ""
    is_configured: bool = False


def read_hugepages_count(path: str = "/proc/sys/vm/nr_hugepages"):
    """读取大页数量，读取失败时返回 None"""
    try:
        return int(Path(path).read_text().strip())
    except (OSError, PermissionError, ValueError):
        return None


def hugepages_target(count=None) -> int:
    """目标页数，未指定时读取 HUGEPAGES_COUNT，默认 8192"""
    return int(count) if count is not None else int(os.getenv("HUGEPAGES_COUNT", "8192"))


class HugePageManager:
    """大页内存管理器"""

    def __init__(self, config: HugePageConfig, log: RunLog):
        self.config = config
        self.log = log

    def configure(self) -> HugePageResult:
        """
        执行CPU大页内存配置
        返回: 配置结果
        """
        self.log.info("=" * 40)
        self.log.info("开始配置CPU大页内存")

        result = HugePageResult()

        current = self._current_count()
        if current is not None and current >= self.config.hugepages_count:
            result.is_configured = True
            self.log.ok(f"大页内存已配置: nr_hugepages = {current} (>= {self.config.hugepages_count})，跳过配置")
            self.log.info("=" * 40)
            return result

        result.meminfo_before = self._read_meminfo()
        self.log.info("配置前内存信息:")
        self._log_meminfo(result.meminfo_before)

        self.log.info(f"设置大页数量: {self.config.hugepages_count}")
        if self._write_hugepages():
            result.is_configured = True
            self.log.ok(f"大页内存配置成功: {self.config.hugepages_count} 页")
        else:
            self.log.fail("大页内存配置失败")
            return result

        result.meminfo_after = self._read_meminfo()
        self.log.info("配置后内存信息:")
        self._log_meminfo(result.meminfo_after)

        self._verify_hugepages(result)

        self.log.info("=" * 40)
        if result.is_configured:
            self.log.ok("CPU大页内存配置完成")
        else:
            self.log.fail("CPU大页内存配置失败")

        return result

    def _current_count(self):
        """
        读取当前大页数量
        返回: 当前页数，读取失败时返回 None
        """
        return read_hugepages_count(self.config.nr_hugepages_path)

    def _read_meminfo(self) -> str:
        """
        读取/proc/meminfo内容
        返回: meminfo文本内容
        """
        try:
            return Path(self.config.meminfo_path).read_text()
        except (OSError, PermissionError) as exc:
            self.log.fail(f"读取meminfo失败: {exc}")
            return ""

    def _log_meminfo(self, meminfo: str):
        """
        输出meminfo中与大页相关的行
        参数:
            meminfo: meminfo文本内容
        """
        for line in meminfo.splitlines():
            if "Huge" in line or "MemTotal" in line or "MemFree" in line:
                self.log.info(f"  {line.strip()}")

    def _write_hugepages(self) -> bool:
        """
        写入大页数量到/proc/sys/vm/nr_hugepages
        返回: 是否写入成功
        """
        try:
            nr_path = Path(self.config.nr_hugepages_path)
            nr_path.write_text(str(self.config.hugepages_count))
            return True
        except (OSError, PermissionError) as exc:
            self.log.fail(f"写入nr_hugepages失败: {exc}")
            self.log.info("尝试使用sudo方式写入...")
            return self._write_hugepages_with_sudo()

    def _write_hugepages_with_sudo(self) -> bool:
        """
        使用sudo写入大页数量
        返回: 是否写入成功
        """
        try:
            proc = subprocess.run(
                ["sudo", "bash", "-c",
                 f"echo {self.config.hugepages_count} > {self.config.nr_hugepages_path}"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode == 0:
                return True
            else:
                error_msg = proc.stderr.strip()
                self.log.fail(f"sudo写入失败: {error_msg}")
                return False
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            self.log.fail(f"sudo写入异常: {exc}")
            return False

    def _verify_hugepages(self, result: HugePageResult):
        """
        验证大页配置是否生效
        参数:
            result: 配置结果对象
        """
        try:
            current = Path(self.config.nr_hugepages_path).read_text().strip()
            if int(current) == self.config.hugepages_count:
                self.log.ok(f"验证通过: nr_hugepages = {current}")
            else:
                self.log.fail(f"验证失败: 期望 {self.config.hugepages_count}, 实际 {current}")
                result.is_configured = False
        except (OSError, ValueError, PermissionError) as exc:
            self.log.fail(f"验证读取失败: {exc}")
            result.is_configured = False


def hugepages_configured(count=None) -> bool:
    """当前大页数量是否已达到目标值"""
    current = read_hugepages_count()
    return current is not None and current >= hugepages_target(count)


def ensure_hugepages(log, count=None) -> bool:
    """
    确保本机大页数量达到目标值，已配置则跳过
    参数:
        log: RunLog 对象
        count: 目标页数，None 时读取 HUGEPAGES_COUNT，默认 8192
    返回:
        是否已配置成功
    """
    manager = HugePageManager(HugePageConfig(hugepages_count=hugepages_target(count)), log)
    return manager.configure().is_configured


def config_hugepage_check(args) -> int:
    """
    配置CPU大页内存主入口函数
    参数:
        args: 命令行参数对象
    返回:
        退出码 (0表示成功, 1表示失败)
    """
    log = RunLog(args, "config_hugepage")
    print(f"日志目录: {log.path}")

    return 0 if ensure_hugepages(log) else 1