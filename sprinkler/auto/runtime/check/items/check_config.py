import re
from sprinkler.auto.runtime.check.items.check_item_base import CheckItemBase


class CheckConfig(CheckItemBase):
    """配置一致性检查。"""

    CHECK_NAME = "配置检查"

    @staticmethod
    def get_commands(chip_count: int = 0, slot_id: int = 0, **_kwargs) -> list[str]:
        return ["return", "dis st | no-more"]

    def analyze_log(self) -> bool:
        node_info = self._get_node_info()
        self.run_log.info(f"{self.CHECK_NAME} - {node_info}")

        cc_blocks = self._extract_command_blocks(r"dis st.*no-more")
        if not cc_blocks: 
            self.run_log.fail("  [异常] 无配置信息")
            return False

        for cmd, output in cc_blocks.items():
            for line in output.splitlines():
                line = line.strip()
                if re.match(r"^.+?:\s+.*\.(cc|cfg)\s*$", line):
                    self.run_log.info(f"  {line}")
        return True
