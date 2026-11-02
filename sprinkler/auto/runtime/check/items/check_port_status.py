import re

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
