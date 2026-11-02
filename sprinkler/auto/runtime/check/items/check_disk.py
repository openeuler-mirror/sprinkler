import re

from sprinkler.auto.runtime.check.items.check_item_base import CheckItemBase


class CheckDisk(CheckItemBase):
    """磁盘空间检查(OS)：/home 可用空间需大于 6G。"""

    CHECK_NAME = "磁盘空间检查(OS)"
    CONNECTION_TYPE = "ssh_os"
    TARGET_TYPE = "cpu_os"
    MIN_HOME_AVAIL_GB: float = 6.0
    CHECK_MOUNT: str = "/home"
    MIN_AVAIL_DISPLAY: str = "6G"

    @staticmethod
    def get_commands(chip_count: int = 0, slot_id: int = 0, **_kwargs) -> list[str]:
        return ["df -h"]

    def analyze_log(self) -> bool:
        node_info = self._get_node_info()
        self.run_log.info(f"{self.CHECK_NAME} - {node_info}")

        df_blocks = self._extract_command_blocks(r"df -h")
        if not df_blocks:
            self.run_log.fail("未找到 df -h 命令结果")
            self.anomalies.append("未找到 df -h 命令结果")
            return False

        all_ok = True
        for cmd, block in df_blocks.items():
            avail_str = self._parse_avail(block, self.CHECK_MOUNT)
            if avail_str is None:
                all_ok = False
                self.run_log.fail(f"  [异常] 未找到 {self.CHECK_MOUNT} 挂载点信息")
                self.anomalies.append(f"磁盘空间检查异常: 未找到 {self.CHECK_MOUNT} 挂载点信息")
                continue

            avail_gb = self._to_gb(avail_str)
            min_gb = self.MIN_HOME_AVAIL_GB
            if avail_gb >= min_gb:
                self.run_log.log("PASS", f"  {self.CHECK_MOUNT} 可用空间: {avail_str}, 满足要求(>{self.MIN_AVAIL_DISPLAY})")
            else:
                all_ok = False
                self.run_log.fail(f"  {self.CHECK_MOUNT} 可用空间: {avail_str}, 不满足要求(>{self.MIN_AVAIL_DISPLAY})")
                self.anomalies.append(f"磁盘空间异常: {self.CHECK_MOUNT}可用{avail_str}, 需要大于{self.MIN_AVAIL_DISPLAY}")

        return all_ok

    def _parse_avail(self, block: str, mount: str) -> str | None:
        for line in block.strip().split("\n"):
            parts = line.split()
            if len(parts) >= 6 and parts[5] == mount:
                return parts[3]
        return None

    @staticmethod
    def _to_gb(size_str: str) -> float:
        match = re.match(r"([\d.]+)([TGMK]?)", size_str)
        if not match:
            return 0.0
        value = float(match.group(1))
        unit = match.group(2)
        if unit == "T":
            return value * 1024
        if unit == "G":
            return value
        if unit == "M":
            return value / 1024
        if unit == "K":
            return value / (1024 * 1024)
        return value


def _make_disk_subclass(
    class_name: str, check_name: str, connection_type: str,
    target_type: str, mount: str, min_avail_gb: float,
    min_avail_display: str, commands: list[str] | None = None,
) -> type[CheckDisk]:
    return type(class_name, (CheckDisk,), {
        "CHECK_NAME": check_name,
        "CONNECTION_TYPE": connection_type,
        "TARGET_TYPE": target_type,
        "MIN_HOME_AVAIL_GB": min_avail_gb,
        "CHECK_MOUNT": mount,
        "MIN_AVAIL_DISPLAY": min_avail_display,
        **({"get_commands": staticmethod(lambda chip_count=0, slot_id=0, **_kw: list(commands))} if commands else {}),
    })


CheckDisk1213 = _make_disk_subclass(
    "CheckDisk1213", "磁盘空间检查(1213)", "cpu_1213", "cpu_1213",
    "/opt/vrpv8/home", 0.5, "500M", commands=["sys", "dia", "l s", "df -h", "exit"],
)

CheckDiskBMC = _make_disk_subclass(
    "CheckDiskBMC", "磁盘空间检查(BMC)", "ssh_bmc", "cpu_bmc",
    "/tmp", 300 / 1024, "300M",
)

CheckDiskNPU1213 = _make_disk_subclass(
    "CheckDiskNPU1213", "磁盘空间检查(NPU-1213)", "npu_1213", "npu_1213",
    "/opt/vrpv8/home", 0.5, "500M", commands=["sys", "dia", "l s", "df -h", "exit"],
)

CheckDiskNPUBMC = _make_disk_subclass(
    "CheckDiskNPUBMC", "磁盘空间检查(NPU-BMC)", "ssh_bmc", "npu_bmc",
    "/tmp", 300 / 1024, "300M",
)

CheckDiskSFUBMC = _make_disk_subclass(
    "CheckDiskSFUBMC", "磁盘空间检查(SFU-BMC)", "ssh_bmc", "sfu_bmc",
    "/tmp", 300 / 1024, "300M",
)

CheckDiskSFU1260 = _make_disk_subclass(
    "CheckDiskSFU1260", "磁盘空间检查(SFU-1260)", "telnet_com", "sfu_com",
    "/opt/vrpv8/home", 0.5, "500M", commands=["sys", "dia", "l s", "df -h", "exit"],
)


