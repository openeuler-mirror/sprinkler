"""
设备上线状态检查功能
功能: 检查设备上线情况和上线顺序
支持设备号在 0-71 范围内的任意连续区间
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

from sprinkler.base.log import RunLog

# 设备号合法范围
MIN_DEVICE_ID = 0
MAX_DEVICE_ID = 71

DAVINCI_ID_PATTERN = re.compile(r"davinci(\d+)")
NAMESPACE_DEVICE_LINE_PATTERN = re.compile(r"\s*(\d+)\s+(\d+)")


@dataclass
class DeviceOnlineConfig:
    """设备上线检查配置参数"""
    expected_count: int = 8
    namespace_node_path: str = "/proc/uda/namespace_node"


@dataclass
class DeviceOnlineResult:
    """设备上线检查结果"""
    davinci_count: int = 0
    expected_count: int = 0
    device_ids: list[int] = None
    is_online_count_ok: bool = False
    is_sequence_ok: bool = False

    def __post_init__(self):
        if self.device_ids is None:
            self.device_ids = []


class DeviceChecker:
    """设备检查器"""

    def __init__(self, config: DeviceOnlineConfig, log: RunLog):
        self.config = config
        self.log = log

    def run_check(self) -> bool:
        """
        执行设备上线状态检查
        返回: 检查是否完全通过
        """
        self.log.info("=" * 40)
        self.log.info("开始设备上线状态检查")
        self.log.info(f"期望设备数量: {self.config.expected_count}")
        self.log.info(
            f"设备号区间不固定（合法范围: {MIN_DEVICE_ID}-{MAX_DEVICE_ID}，连续递增即可）"
        )

        count_ok = self._check_device_count()
        if not count_ok:
            return False

        sequence_ok = self._check_device_sequence()
        if not sequence_ok:
            return False

        self.log.info("设备上线状态检查通过，环境正常")
        return True

    def _check_device_count(self) -> bool:
        """
        检查/dev/目录下的davinci设备文件数量
        返回: 数量是否匹配期望值
        """
        davinci_ids = self._get_davinci_ids()
        davinci_count = len(davinci_ids)

        if davinci_count != self.config.expected_count:
            self.log.fail(
                f"当前仅上线 {davinci_count} 张卡 (期望: {self.config.expected_count})"
            )
            self.log.fail("存在设备上线问题，请检查环境")
            return False

        invalid_ids = [i for i in davinci_ids if not MIN_DEVICE_ID <= i <= MAX_DEVICE_ID]
        if invalid_ids:
            self.log.fail(
                f"存在超出合法范围({MIN_DEVICE_ID}-{MAX_DEVICE_ID})"
                f"的davinci设备号: {sorted(invalid_ids)}"
            )
            return False

        self.log.info(
            f"已成功上线 {davinci_count} 张卡, "
            f"设备号范围: {min(davinci_ids)}-{max(davinci_ids)}"
        )
        return True

    def _get_davinci_ids(self) -> list[int]:
        """
        获取/dev/目录下davinci设备号列表
        返回: davinci设备号列表
        """
        try:
            dev_dir = Path("/dev/")
            if not dev_dir.exists():
                return []
            davinci_ids = []
            for d in dev_dir.glob("davinci[0-9]*"):
                match = DAVINCI_ID_PATTERN.fullmatch(d.name)
                if match:
                    davinci_ids.append(int(match.group(1)))
            return davinci_ids
        except (OSError, PermissionError):
            return []

    def _check_device_sequence(self) -> bool:
        """
        检查/proc/uda/namespace_node中的设备上线顺序
        返回: 顺序是否正常
        """
        namespace_path = Path(self.config.namespace_node_path)

        if not namespace_path.exists():
            self.log.fail(f"文件 {self.config.namespace_node_path} 不存在")
            return False

        try:
            device_ids = self._parse_namespace_node(namespace_path)
        except Exception as exc:
            self.log.fail(f"解析namespace_node文件失败: {exc}")
            return False

        if len(device_ids) != self.config.expected_count:
            self.log.fail(
                f"设备ID数量异常: 期望{self.config.expected_count}, 实际{len(device_ids)}"
            )
            self._log_namespace_content(namespace_path)
            self.log.fail("namespace_node 设备数量与期望不符，请检查环境")
            return False

        if not self._validate_sequence(device_ids):
            self.log.fail("设备上线顺序异常:")
            self._log_namespace_content(namespace_path)
            self.log.fail("设备上线顺序存在问题，请检查环境")
            return False

        self.log.info(
            f"设备上线顺序正常，连续区间: {device_ids[0]}-{device_ids[-1]}"
        )
        return True

    def _parse_namespace_node(self, namespace_path: Path) -> list[int]:
        """
        解析namespace_node文件，提取第一个namespace分组的设备ID列表
        """
        device_ids = []
        content = namespace_path.read_text()

        for line in content.splitlines():
            match = NAMESPACE_DEVICE_LINE_PATTERN.match(line)
            if match:
                device_ids.append(int(match.group(2)))
            elif device_ids:
                # 第一个分组的数字行已结束（空行或下一个分组头），停止
                break

        return device_ids

    def _validate_sequence(self, device_ids: list[int]) -> bool:
        """
        验证设备ID序列是否为合法范围内的连续递增区间
        """
        if not device_ids:
            return False

        for i, device_id in enumerate(device_ids):
            if not MIN_DEVICE_ID <= device_id <= MAX_DEVICE_ID:
                self.log.fail(
                    f"设备号 {device_id} 超出合法范围 "
                    f"{MIN_DEVICE_ID}-{MAX_DEVICE_ID}"
                )
                return False
            if i > 0 and device_id != device_ids[i - 1] + 1:
                return False

        return True

    def _log_namespace_content(self, namespace_path: Path):
        """
        输出namespace_node文件内容
        """
        try:
            content = namespace_path.read_text()
            for line in content.splitlines():
                self.log.info(line)
        except Exception:
            pass


def device_online_check(args) -> int:
    """
    设备上线状态检查主入口函数
    环境变量:
        EXPECTED_COUNT: 期望设备数量，默认8
        NAMESPACE_NODE_PATH: namespace_node文件路径
    """
    log = RunLog(args, "device_online")
    print(f"日志目录: {log.path}")

    expected_count = int(os.getenv("EXPECTED_COUNT", "8"))

    config = DeviceOnlineConfig(
        expected_count=expected_count,
        namespace_node_path=os.getenv("NAMESPACE_NODE_PATH", "/proc/uda/namespace_node"),
    )

    checker = DeviceChecker(config, log)
    success = checker.run_check()

    if success:
        return 0
    else:
        return 1
