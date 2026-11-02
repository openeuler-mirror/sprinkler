import re

from sprinkler.auto.runtime.check.items.check_item_base import CheckItemBase


class CheckCardRegister(CheckItemBase):
    """子卡注册检查：期望所有子卡均已注册，且子卡数量符合预期。"""

    CHECK_NAME = "子卡注册检查"
    EXPECTED_CARD_COUNT: int = 0

    @staticmethod
    def get_commands(chip_count: int = 0, slot_id: int = 0, **_kwargs) -> list[str]:
        return ["return", "dis dev card | no-more"]

    def analyze_log(self) -> bool:
        node_info = self._get_node_info()
        self.run_log.info(f"{self.CHECK_NAME} - {node_info}")

        card_blocks = self._extract_command_blocks(r"dis dev card.*")
        if not card_blocks:
            self.run_log.fail("未找到子卡注册命令结果")
            self.anomalies.append("未找到子卡注册命令结果")
            return False

        all_ok = True
        for cmd, block in card_blocks.items():
            self.run_log.info(f"命令: {cmd}")
            unregistered, card_count = self._parse_card_status(block.strip().split("\n"))
            if unregistered:
                all_ok = False
                self.run_log.fail(f"  [异常] 以下子卡未正常注册:")
                for card in unregistered:
                    self.run_log.fail(f"    - {card}")
                self.anomalies.append(f"子卡注册异常: {len(unregistered)}张子卡未注册")
            expected = self.EXPECTED_CARD_COUNT
            if expected and card_count != expected:
                all_ok = False
                self.run_log.fail(
                    f"  [异常] 子卡数量不符: 实际{card_count}张, 预期{expected}张"
                )
                self.anomalies.append(
                    f"子卡数量异常: 实际{card_count}张, 预期{expected}张"
                )
            if not unregistered and (not expected or card_count == expected):
                self.run_log.log("PASS", f"  [正常] 所有子卡均已注册(共{card_count}张)")
        return all_ok

    def _parse_card_status(self, lines: list[str]) -> tuple[list[str], int]:
        unregistered = []
        card_count = 0
        for line in lines:
            line = line.strip()
            if not line or line.startswith("---") or line.startswith("Slot"):
                continue
            parts = re.split(r"\s{2,}", line)
            if len(parts) >= 5 and re.match(r"\d+/\d+", parts[0]):
                card_count += 1
                register_status = parts[4].strip()
                if register_status != "Registered":
                    unregistered.append(f"{parts[0]} ({register_status})")
        return unregistered, card_count
