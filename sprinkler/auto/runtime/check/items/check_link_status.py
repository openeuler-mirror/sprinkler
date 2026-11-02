import re

from sprinkler.auto.runtime.check.items.check_item_base import CheckItemBase


class CheckLinkStatus(CheckItemBase):
    """建链状态检查：期望所有 peer 均为 Established。"""

    CHECK_NAME = "建链状态检查"
    CHIP_COUNT: int = 4

    @staticmethod
    def get_commands(chip_count: int = 0, slot_id: int = 0, **_kwargs) -> list[str]:
        return ["return"] + [f"dis lbnp peer vpn-instance ub-instance-{i}" for i in range(chip_count)]

    def analyze_log(self) -> bool:
        node_info = self._get_node_info()
        self.run_log.info(f"{self.CHECK_NAME} - {node_info}")

        lbnp_blocks = self._extract_command_blocks(r"dis lbnp peer vpn-instance ub-instance-\d+")
        if not lbnp_blocks:
            self.run_log.fail("未找到建链命令结果")
            self.anomalies.append("未找到建链命令结果")
            return False

        all_ok = True
        chip_count = 0
        for cmd, block in lbnp_blocks.items():
            chip_match = re.search(r"ub-instance-(\d+)", cmd)
            chip_num = chip_match.group(1) if chip_match else "未知"
            lines = block.strip().split("\n")
            has_peers = any("Total number of peers" in line for line in lines)
            if not has_peers:
                self.run_log.info(f"  芯片{chip_num}: 无建链信息(可能无peer)")
                continue
            chip_count += 1
            non_established = self._find_non_established(lines)
            if non_established:
                all_ok = False
                self.run_log.fail(f"  芯片{chip_num} [异常]:")
                for port, state in non_established:
                    self.run_log.fail(f"    - {port} State: {state}")
                self.anomalies.append(f"建链异常: 芯片{chip_num}有{len(non_established)}个非Established端口")
            else:
                self.run_log.log("PASS", f"  芯片{chip_num} [正常]: 全部Established")

        self.run_log.info(f"  共查询 {chip_count} 个芯片的建链状态")
        return all_ok

    def _find_non_established(self, lines: list[str]) -> list[tuple[str, str]]:
        non_established = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 3 and parts[0].startswith(("400G", "800G")):
                state = parts[2]
                if state != "Established":
                    non_established.append((parts[0], state))
        return non_established
