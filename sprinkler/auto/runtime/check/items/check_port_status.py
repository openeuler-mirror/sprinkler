import re

from sprinkler.auto.runtime.check.check_env import (
    EXPECTED_LANE_COUNT_5808,
    EXPECTED_PORT_COUNT_5808,
    PAGINATION_OFF_5808,
    PORT_PREFIX_5808,
    parse_5808_port_status,
)
from sprinkler.auto.runtime.check.items.check_item_base import CheckItemBase


class CheckPortStatus(CheckItemBase):
    """端口状态检查：异常 down 端口不在允许列表则告警。"""

    CHECK_NAME = "端口状态检查"
    ALLOWED_DOWN_PATTERN: str | None = None

    @staticmethod
    def get_commands(chip_count: int = 0, slot_id: int = 0, **_kwargs) -> list[str]:
        return ["return", "dis int b | inc down | no-more"]

    def analyze_log(self) -> bool:
        node_info = self._get_node_info()
        self.run_log.info(f"{self.CHECK_NAME} - {node_info}")

        port_blocks = self._extract_command_blocks(r"dis int b.*inc down.*no-more")
        if not port_blocks:
            self.run_log.fail("未找到端口状态命令结果")
            self.anomalies.append("未找到端口状态命令结果")
            return False

        allowed_re = re.compile(self.ALLOWED_DOWN_PATTERN or r"^$")
        all_ok = True
        for cmd, block in port_blocks.items():
            self.run_log.info(f"命令: {cmd}")
            down_ports = self._find_abnormal_down_ports(block, allowed_re)
            if down_ports:
                all_ok = False
                self.run_log.fail(f"  [异常] 以下down端口不在允许列表中:")
                for p in down_ports:
                    self.run_log.fail(f"    - {p}")
                self.anomalies.append(f"端口状态异常: {len(down_ports)}个down端口不在允许列表")
            else:
                self.run_log.log("PASS", f"  [正常] 所有down端口均在允许列表中")
        return all_ok

    def _find_abnormal_down_ports(self, block: str, allowed_re: re.Pattern) -> list[str]:
        down_ports = []
        for line in block.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 3 and parts[1] == "down" and parts[2] == "down":
                if not allowed_re.match(parts[0]):
                    down_ports.append(parts[0])
        return down_ports


class _Check5808PortList(CheckItemBase):
    """5808 端口列表检查公共流程：命令块提取、端口数量校验、异常汇总。"""

    COMMAND: str = ""
    PORT_PREFIX: str = PORT_PREFIX_5808
    EXPECTED_PORT_COUNT: int = EXPECTED_PORT_COUNT_5808
    BLOCK_MISSING_MESSAGE: str = ""
    ABNORMAL_TITLE: str = ""
    ABNORMAL_SUMMARY: str = "端口异常: {count}个端口"
    PASS_MESSAGE: str = ""

    def analyze_log(self) -> bool:
        node_info = self._get_node_info()
        self.run_log.info(f"{self.CHECK_NAME} - {node_info}")

        port_blocks = self._extract_command_blocks(rf"^{re.escape(self.COMMAND)}$")
        if not port_blocks:
            self.run_log.fail(self.BLOCK_MISSING_MESSAGE)
            self.anomalies.append(self.BLOCK_MISSING_MESSAGE)
            return False

        all_ok = True
        for cmd, block in port_blocks.items():
            self.run_log.info(f"命令: {cmd}")
            rows = self.parse_rows(block)
            count = len(rows)
            if count != self.EXPECTED_PORT_COUNT:
                all_ok = False
                self.run_log.fail(
                    f"  [异常] {self.PORT_PREFIX} 端口数量不符: "
                    f"实际{count}个, 预期{self.EXPECTED_PORT_COUNT}个"
                )
                self.anomalies.append(
                    f"端口数量异常: 实际{count}个, 预期{self.EXPECTED_PORT_COUNT}个"
                )

            abnormal_ports = self.find_abnormal_ports(rows)
            if abnormal_ports:
                all_ok = False
                self.run_log.fail(f"  [异常] {self.ABNORMAL_TITLE}:")
                for port in abnormal_ports:
                    self.run_log.fail(f"    - {port}")
                self.anomalies.append(
                    self.ABNORMAL_SUMMARY.format(count=len(abnormal_ports))
                )
            if not abnormal_ports and count == self.EXPECTED_PORT_COUNT:
                self.run_log.log("PASS", self.pass_message(count))
        return all_ok

    def pass_message(self, count: int) -> str:
        return self.PASS_MESSAGE.format(count=count)

    def parse_rows(self, block: str) -> list:
        raise NotImplementedError

    def find_abnormal_ports(self, rows: list) -> list[str]:
        raise NotImplementedError


class CheckPort5808(_Check5808PortList):
    """端口状态检查(5808)：800GUB 端口的 PHY 和 Protocol 必须全部为 up。"""

    CHECK_NAME = "端口状态检查({label})"
    COMMAND = "dis int b"
    BLOCK_MISSING_MESSAGE = "未找到 dis int b 命令结果"
    ABNORMAL_TITLE = f"以下 {PORT_PREFIX_5808} 端口未全部 up"
    ABNORMAL_SUMMARY = "端口状态异常: {count}个端口未全部up"
    PASS_MESSAGE = f"  [正常] 所有 {PORT_PREFIX_5808} 端口均为 up(共{{count}}个)"

    @staticmethod
    def get_commands(chip_count: int = 0, slot_id: int = 0, **_kwargs) -> list[str]:
        return [PAGINATION_OFF_5808, "dis int b"]

    def parse_rows(self, block: str) -> list[tuple[str, str, str]]:
        rows = []
        for line in block.strip().split("\n"):
            parts = line.split()
            if len(parts) < 3 or not parts[0].startswith(self.PORT_PREFIX):
                continue
            rows.append((parts[0], parts[1], parts[2]))
        return rows

    def find_abnormal_ports(self, rows: list[tuple[str, str, str]]) -> list[str]:
        return [
            f"{name} PHY={phy} Protocol={protocol}"
            for name, phy, protocol in rows
            if phy != "up" or protocol != "up"
        ]


class CheckLane5808(_Check5808PortList):
    """Lane 状态检查(5808)：所有 up 端口的 running-lane-num 必须为 4（满 lane）。"""

    CHECK_NAME = "Lane状态检查({label})"
    COMMAND = "dis int info"
    EXPECTED_LANE_COUNT: int = EXPECTED_LANE_COUNT_5808
    BLOCK_MISSING_MESSAGE = "未找到 dis int info 命令结果"
    ABNORMAL_TITLE = f"以下端口 running-lane-num 未达到 {EXPECTED_LANE_COUNT_5808}（降lane）"
    ABNORMAL_SUMMARY = "Lane状态异常: {count}个端口降lane"
    PASS_MESSAGE = (
        f"  [正常] 所有 {PORT_PREFIX_5808} 端口均为满 lane"
        f"({EXPECTED_LANE_COUNT_5808})(共{{count}}个)"
    )
    _ROW_RE = re.compile(r"^(\S+)\s+(\d+)\s+(\d+)$")

    @staticmethod
    def get_commands(chip_count: int = 0, slot_id: int = 0, **_kwargs) -> list[str]:
        return [PAGINATION_OFF_5808, "dis int b", "dis int info"]

    def analyze_log(self) -> bool:
        self._ignored_down_ports = set()
        blocks = self._extract_command_blocks(r"^dis int b$")
        if not blocks:
            message = "未找到 dis int b 命令结果，无法忽略 down 端口"
            self.run_log.fail(message)
            self.anomalies.append(message)
            return False
        ports, self._ignored_down_ports = parse_5808_port_status(
            next(iter(blocks.values()))
        )
        if len(ports) != self.EXPECTED_PORT_COUNT:
            message = (
                f"dis int b 端口数量异常: 实际{len(ports)}个, "
                f"预期{self.EXPECTED_PORT_COUNT}个"
            )
            self.run_log.fail(f"  [异常] {message}")
            self.anomalies.append(message)
            return False
        self.run_log.info(f"  忽略 {len(self._ignored_down_ports)} 个 down 端口")
        return super().analyze_log()

    def parse_rows(self, block: str) -> list[tuple[str, int, int]]:
        rows = []
        for line in block.strip().split("\n"):
            match = self._ROW_RE.match(line.strip())
            if not match:
                continue
            name = match.group(1)
            if not name.startswith(self.PORT_PREFIX):
                continue
            rows.append((name, int(match.group(2)), int(match.group(3))))
        return rows

    def find_abnormal_ports(self, rows: list[tuple[str, int, int]]) -> list[str]:
        ignored = getattr(self, "_ignored_down_ports", set())
        return [
            f"{name} running-lane-num={running}, real-lane-num={real}, "
            f"期望={self.EXPECTED_LANE_COUNT}(降lane)"
            for name, running, real in rows
            if name not in ignored and running != self.EXPECTED_LANE_COUNT
        ]

    def pass_message(self, count: int) -> str:
        ignored = getattr(self, "_ignored_down_ports", set())
        if ignored:
            return (
                f"  [正常] 所有 up 端口均为满 lane({self.EXPECTED_LANE_COUNT})"
                f"(忽略{len(ignored)}个down端口)"
            )
        return super().pass_message(count)
