import re

from sprinkler.auto.runtime.check.items.check_item_base import CheckItemBase


class CheckUbmConn(CheckItemBase):
    """UBM连接数检查：检查设备与UBM的连接数量是否在正常范围内。"""

    CHECK_NAME = "UBM连接数检查"
    
    UBM_CONN_MIN: int = 0
    UBM_CONN_MAX: int = 20

    @staticmethod
    def get_commands(chip_count: int = 0, slot_id: int = 0, **_kwargs) -> list[str]:
        return [
            "display users",
        ]

    def analyze_log(self) -> bool:
        node_info = self._get_node_info()
        self.run_log.info(f"{self.CHECK_NAME} - {node_info}")

        user_blocks = self._extract_command_blocks(r"display users")
        
        if not user_blocks:
            self.run_log.fail("未找到 display users 命令结果")
            self.anomalies.append("未找到 display users 命令结果")
            return False

        all_ok = True
        for cmd, block in user_blocks.items():
            lines = block.strip().split("\n")
            conn_count = self._parse_users_count(lines)

            if conn_count is None:
                all_ok = False
                self.run_log.fail("  [异常] 无法解析 display users 结果")
                self.anomalies.append("无法解析 display users 结果")
            elif conn_count < self.UBM_CONN_MIN or conn_count > self.UBM_CONN_MAX:
                all_ok = False
                self.run_log.fail(f"  [异常] UBM连接数: {conn_count} (超出正常范围: {self.UBM_CONN_MIN}-{self.UBM_CONN_MAX})")
                self.anomalies.append(f"UBM连接数异常: {conn_count} (期望: {self.UBM_CONN_MIN}-{self.UBM_CONN_MAX})")
            else:
                self.run_log.log("PASS", f"  [正常] UBM连接数: {conn_count}")

        return all_ok

    def _parse_users_count(self, lines: list[str]) -> int | None:
        """
        解析特定格式的 display users 输出。
        逻辑：
        1. 过滤空行。
        2. 找到并跳过表头行（包含 User-Intf）。
        3. 检查下一行是否为分隔线（短横杠），如果是则跳过。
        4. 统计剩余的所有行，即为数据行数量。
        """
        if not lines:
            return 0

        # 1. 清洗空行
        valid_lines = [line.strip() for line in lines if line.strip()]
        
        if not valid_lines:
            return 0

        # 2. 寻找表头行
        header_idx = -1
        for i, line in enumerate(valid_lines):
            # 特征：包含 "User-Intf"
            if "User-Intf" in line:
                header_idx = i
                break
        
        if header_idx == -1:
            return None  # 无法找到表头

        # 3. 确定数据起始位置
        # 从表头行的下一行开始
        data_start_idx = header_idx + 1
        
        # 4. 检查并跳过分隔线
        # 如果表头行后面还有一行，且该行主要由 '-' 组成，则跳过它
        if data_start_idx < len(valid_lines):
            separator_line = valid_lines[data_start_idx]
            # 判断是否为分隔线：非空且主要由 '-' 组成（允许少量空格）
            # 正则：开头非空，中间全是 '-' 或空格，或者全是 '-'
            if re.fullmatch(r'^[\s\-]+$', separator_line):
                data_start_idx += 1

        # 5. 统计数据行
        # 从 data_start_idx 开始，直到列表结束，所有非空行都算作数据行
        count = len(valid_lines) - data_start_idx
        return max(0, count)