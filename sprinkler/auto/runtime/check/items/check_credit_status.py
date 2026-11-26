import re

from sprinkler.auto.runtime.check.check_env import (
    EXPECTED_PORT_COUNT_5808,
    PAGINATION_OFF_5808,
    build_5808_interfaces,
    parse_5808_port_status,
)
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


# ======================================================================
# 5808 前台交换机 Credit 检查：逐接口查询并与基线 Available(cell) 比对
# ======================================================================

# 基线：VL0=48、VL1~VL11=28、Shared=686。
EXPECTED_VL_AVAILABLE = {0: 48, **{vl: 28 for vl in range(1, 12)}}
EXPECTED_SHARED_AVAILABLE = 686


class CheckCredit5808(CheckItemBase):
    """Credit 状态检查(5808)：每个接口的 VL/Shared Available(cell) 必须与基线一致。"""

    CHECK_NAME = "Credit状态检查({label})"
    _INTERFACE_RE = re.compile(r"dis qos port-credit state interface (\S+)")
    _VL_RE = re.compile(r"^\s*(\d+)\s+(\S+)\s+(\d+)\s*$")
    _SHARED_RE = re.compile(r"^\s*Shared:\s+(\S+)\s+(\d+)\s*$", re.MULTILINE)

    @staticmethod
    def interfaces() -> list[str]:
        return build_5808_interfaces()

    @classmethod
    def get_commands(cls, chip_count: int = 0, slot_id: int = 0, **_kwargs) -> list[str]:
        # 首轮只查端口状态，credit 命令待解析出 up 接口后再生成。
        return [PAGINATION_OFF_5808, "dis int b"]

    @classmethod
    def get_deferred_commands(cls, results: list[dict] | None = None, **_kwargs) -> list[str]:
        """依据 dis int b 结果，只为 up 接口生成 credit 查询命令。"""
        status_result = next(
            (r for r in results or [] if r.get("cmd") == "dis int b" and r.get("stdout")),
            None,
        )
        if status_result is None:
            return []
        _, down_ports = parse_5808_port_status(status_result["stdout"])
        up_interfaces = [iface for iface in cls.interfaces() if iface not in down_ports]
        if not up_interfaces:
            return []
        # 延迟命令会在新的 SSH 会话中执行，需重新关闭分页。
        return [PAGINATION_OFF_5808] + [
            f"dis qos port-credit state interface {iface}" for iface in up_interfaces
        ]

    def analyze_log(self) -> bool:
        node_info = self._get_node_info()
        self.run_log.info(f"{self.CHECK_NAME} - {node_info}")

        status_blocks = self._extract_command_blocks(r"^dis int b$")
        if not status_blocks:
            message = "未找到 dis int b 命令结果，无法忽略 down 端口"
            self.run_log.fail(message)
            self.anomalies.append(message)
            return False
        status_ports, ignored = parse_5808_port_status(
            next(iter(status_blocks.values()))
        )
        if len(status_ports) != EXPECTED_PORT_COUNT_5808:
            message = (
                f"dis int b 端口数量异常: 实际{len(status_ports)}个, "
                f"预期{EXPECTED_PORT_COUNT_5808}个"
            )
            self.run_log.fail(f"  [异常] {message}")
            self.anomalies.append(message)
            return False
        self.run_log.info(f"  忽略 {len(ignored)} 个 down 端口")

        credit_blocks = self._extract_command_blocks(
            r"dis qos port-credit state interface \S+"
        )
        if not credit_blocks:
            self.run_log.fail("未找到 credit 查询命令结果")
            self.anomalies.append("未找到 credit 查询命令结果")
            return False

        blocks_by_interface = {}
        for cmd, block in credit_blocks.items():
            match = self._INTERFACE_RE.search(cmd)
            if match:
                blocks_by_interface[match.group(1)] = block

        expected_interfaces = [
            iface for iface in self.interfaces() if iface not in ignored
        ]
        if not expected_interfaces:
            self.run_log.info("  所有接口均为 down，跳过 credit 检查")
            return True

        all_ok = True
        problem_count = 0
        for iface in expected_interfaces:
            block = blocks_by_interface.get(iface)
            if block is None:
                all_ok = False
                problems = [f"{iface}: 未找到 credit 查询结果"]
            else:
                problems = self._analyze_block(iface, block)
            if not problems:
                continue
            all_ok = False
            problem_count += len(problems)
            for problem in problems:
                self.run_log.fail(f"    - {problem}")
            self.anomalies.extend(problems)

        if problem_count == 0:
            pass_message = (
                f"  [正常] 所有 {len(expected_interfaces)} 个接口 credit 与基线一致"
            )
            if ignored:
                pass_message += f"(忽略 {len(ignored)} 个 down 接口)"
            self.run_log.log("PASS", pass_message)
        return all_ok

    def _analyze_block(self, iface: str, block: str) -> list[str]:
        vl_values = {}
        for line in block.splitlines():
            match = self._VL_RE.match(line)
            if match:
                vl_values[int(match.group(1))] = int(match.group(3))

        shared_match = self._SHARED_RE.search(block)
        if not vl_values and shared_match is None:
            return [f"{iface}: 未找到 credit 表"]

        problems = []
        for vl, expected in EXPECTED_VL_AVAILABLE.items():
            actual = vl_values.get(vl)
            if actual is None:
                problems.append(f"{iface}: VL{vl} 无 Available 数据")
            elif actual != expected:
                problems.append(
                    f"{iface}: VL{vl} Available={actual}, 期望={expected}"
                )

        unexpected_vls = sorted(set(vl_values) - set(EXPECTED_VL_AVAILABLE))
        if unexpected_vls:
            problems.append(f"{iface}: 出现基线外 VL {unexpected_vls}")

        if shared_match is None:
            problems.append(f"{iface}: 无 Shared Available 数据")
        else:
            shared = int(shared_match.group(2))
            if shared != EXPECTED_SHARED_AVAILABLE:
                problems.append(
                    f"{iface}: Shared Available={shared}, 期望={EXPECTED_SHARED_AVAILABLE}"
                )
        return problems
