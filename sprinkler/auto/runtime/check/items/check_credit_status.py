import re

from sprinkler.auto.runtime.check.items.check_item_base import CheckItemBase


class CheckCreditStatus(CheckItemBase):
    """信用证状态检查：QOS USED CREDIT TABLE 中 total 不为 0 则异常。"""

    CHECK_NAME = "信用证状态检查"
    CHIP_COUNT: int = 4
    SLOT_ID: int = 0

    @staticmethod
    def get_commands(chip_count: int = 0, slot_id: int = 0,credit_after_lines: int = 18) -> list[str]:
        commands = ["sys", "dia"]
        for i in range(chip_count):
            commands.append(
                f'dis forward information enp slot {slot_id} chip {i} '
                f'"get oda table qos credit current" | after {credit_after_lines} inc USED | exclude " 0$" | exclude total | exclude port8 | exclude port17'
            )
        return commands
    
    @staticmethod
    def analyze_single_command(cmd: str, stdout: str, target: str, run_log):
        chip_match = re.search(r"chip (\d+)", cmd)
        chip_num = chip_match.group(1) if chip_match else "未知"
        
        if 'dis forward information enp slot' not in cmd:
            return
        if 'QOS USED CREDIT TABLE' not in stdout:
            run_log.fail(f"  {target}——芯片{chip_num} [异常]: 指令执行异常，未查询到 QOS USED CREDIT TABLE, 建议手动检查")
            return
        
        has_error = False
        non_zero_lines, abnormal_down_lines = CheckCreditStatus._check_credits(stdout)
        
        if non_zero_lines:
            has_error = True
            run_log.fail(f"  {target}——芯片{chip_num} [异常]: QOS USED CREDIT TABLE 中total不为0")
            for line in non_zero_lines:
                run_log.fail(f"    - {line}")
        if abnormal_down_lines:
            has_error = True
            run_log.fail(f"  {target}——芯片{chip_num} [异常]: QOS USED CREDIT TABLE 中端口异常down")
            for line in abnormal_down_lines:
                run_log.fail(f"    - {line}")     
        if not has_error:
            run_log.log("PASS", f"  {target}——芯片{chip_num} [正常]")

    def analyze_log(self) -> bool:
        credit_blocks = self._extract_command_blocks(
            r'dis forward information enp slot \d+ chip \d+ "get oda table qos credit current" .* after \d+ inc USED .* exclude'
        )
        if not credit_blocks:
            self.run_log.fail("未找到信用证命令结果")
            self.anomalies.append("未找到信用证命令结果")
            return False

        all_ok = True
        chip_count = 0
        for cmd, block in credit_blocks.items():
            chip_match = re.search(r"chip (\d+)", cmd)
            chip_num = chip_match.group(1) if chip_match else "未知"
            chip_count += 1
            non_zero_lines, abnormal_down_lines = CheckCreditStatus._check_credits(block)
            if 'QOS USED CREDIT TABLE' not in block:
                all_ok = False
                self.anomalies.append(f"芯片{chip_num} [查询异常]: 指令执行异常，未查询到 QOS USED CREDIT TABLE, 建议手动检查")
            if non_zero_lines:
                all_ok = False
                self.anomalies.append(f"信用证异常: 芯片{chip_num}有{len(non_zero_lines)}行total不为0")
            if abnormal_down_lines:
                all_ok = False
                self.anomalies.append(f"信用证异常: 芯片{chip_num}有{len(abnormal_down_lines)}行端口异常down")

        return all_ok
    
    @staticmethod
    def _check_credits(block: str) -> tuple[list[str], list[str]]:
        non_zero = []
        abnormal_down = []
        lines = block.strip().split("\n")
        in_used = False
        for line in lines:
            line = line.strip()
            if "QOS USED CREDIT TABLE:" in line:
                in_used = True
                continue
            if in_used:
                if not line:
                    continue
                if "down" in line:
                    abnormal_down.append(line)
                parts = line.split()
                if parts:
                    try:
                        total = int(parts[-1])
                        if total != 0:
                            non_zero.append(line)
                    except ValueError:
                        pass
        return non_zero, abnormal_down
