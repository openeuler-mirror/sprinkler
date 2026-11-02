"""
配置CPU大页内存功能
功能: 开启CPU大页内存(HugePages), 不配置寄存器
"""

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

    config = HugePageConfig(
        hugepages_count=int(__import__("os").getenv("HUGEPAGES_COUNT", "8192")),
    )

    manager = HugePageManager(config, log)
    result = manager.configure()

    return 0 if result.is_configured else 1