from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Sequence

from sprinkler.connect.connection_runner import create_runner
from sprinkler.connect.targets import ConnectionTarget

MAPPING_COMMAND = "dis fei fe port mapping slot {slot}"
SET_PRBS_COMMAND = ('display forward information enp slot {slot} chip {chip} '
                    '"set lsw port prbs {port} enable={state},pattern=p31"')
GET_PRBS_COMMAND = ('display forward information enp slot {slot} chip {chip} '
                    '"get lsw port prbs {port}"')
# 进入诊断视图后 ``display forward information ...`` 才可用。
SETUP_COMMANDS = ("system-view", "diagnose")
# 关闭分页，避免端口映射输出被 ---- More ---- 截断。
PAGINATION_OFF = "screen-length 0 temporary"

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
PORT_LINE_RE = re.compile(
    r"^(?P<interface>\S+)\s+(?P<ifindex>\d+)\s+(?P<tb>\d+)\s+"
    r"(?P<tp>\d+)\s+(?P<chip>\d+)\s+(?P<port>\d+)\s+(?P<core>\d+)\s*$")
PRBS_PORT_RE = re.compile(
    r"\[PRBS\]\s+PORT\s+(?P<port>\d+),\s+EN\s*=\s*(?P<en>\d+)\s+"
    r"P\s*=\s*(?P<lanes>\d+)\s+errNum\s*=\s*(?P<err_num>\d+)")
PRBS_SERDES_RE = re.compile(
    r"\[PRBS\]\s+result of unit\s+(?P<unit>\d+)\s+serdes\s+(?P<serdes>\d+):\s+"
    r"en=(?P<en>\d+),\s+errNum=(?P<err_num>\d+),\s+"
    r"rate=(?P<rate>[0-9.eE+-]+),\s+alos=(?P<alos>\d+)")
ERROR_MARKERS = ("error", "unrecognized", "failed", "failure", "invalid",
                 "wrong parameter", "incomplete command")


@dataclass(frozen=True)
class SwitchPort:
    interface: str
    chip: int
    port: int


@dataclass
class PrbsPortResult:
    port: int
    en: int
    lanes: int
    err_num: int
    serdes: List[dict] = field(default_factory=list)


def parse_port_mapping(text: str) -> List[SwitchPort]:
    """解析 ``dis fei fe port mapping`` 输出，返回去重后的 (chip, port)。"""
    ports = []
    seen = set()
    for raw in _clean(text).splitlines():
        match = PORT_LINE_RE.match(raw.strip())
        if not match:
            continue
        chip = int(match.group("chip"))
        port = int(match.group("port"))
        if (chip, port) in seen:
            continue
        seen.add((chip, port))
        ports.append(SwitchPort(match.group("interface"), chip, port))
    return ports


def parse_prbs_output(text: str) -> PrbsPortResult | None:
    """解析 ``get lsw port prbs`` 输出；端口汇总行缺失时返回 None。"""
    cleaned = _clean(text)
    summary = PRBS_PORT_RE.search(cleaned)
    if summary is None:
        return None
    serdes = []
    for match in PRBS_SERDES_RE.finditer(cleaned):
        serdes.append({
            "unit": int(match.group("unit")),
            "serdes": int(match.group("serdes")),
            "en": int(match.group("en")),
            "err_num": int(match.group("err_num")),
            "rate": match.group("rate"),
            "alos": int(match.group("alos")),
        })
    return PrbsPortResult(
        port=int(summary.group("port")),
        en=int(summary.group("en")),
        lanes=int(summary.group("lanes")),
        err_num=int(summary.group("err_num")),
        serdes=serdes,
    )


def set_prbs_command(slot: int, port: SwitchPort, enable: bool) -> str:
    state = "ON" if enable else "OFF"
    return SET_PRBS_COMMAND.format(slot=int(slot), chip=port.chip,
                                   port=port.port, state=state)


def get_prbs_command(slot: int, port: SwitchPort) -> str:
    return GET_PRBS_COMMAND.format(slot=int(slot), chip=port.chip, port=port.port)


def command_succeeded(result: dict) -> bool:
    """交互式 shell 无退出码，用输出中的错误关键字判断命令是否成功。"""
    if not result.get("ok"):
        return False
    text = f"{result.get('stdout', '')}\n{result.get('stderr', '')}".lower()
    return not any(marker in text for marker in ERROR_MARKERS)


def check_prbs_ready(ports: Sequence[SwitchPort],
                     results: Sequence[dict]) -> List[str]:
    """校验一轮 get lsw port prbs：每个端口 EN=1 且每条 serdes en=1。"""
    problems = []
    for index, port in enumerate(ports):
        result = results[index] if index < len(results) else {}
        label = f"chip{port.chip} port{port.port}"
        if not command_succeeded(result):
            problems.append(f"{label} 状态读取失败: {_reason(result)}")
            continue
        parsed = parse_prbs_output(result.get("stdout", ""))
        if parsed is None:
            problems.append(f"{label} 状态输出无法解析")
            continue
        if parsed.en != 1:
            problems.append(f"{label} EN={parsed.en}")
        if not parsed.serdes:
            problems.append(f"{label} 无 serdes 读数")
            continue
        for lane in parsed.serdes:
            if lane["en"] != 1:
                problems.append(f"{label} unit{lane['unit']} "
                                f"serdes{lane['serdes']}: en={lane['en']}")
    return problems


class Switch5808:
    """5808 前台交换机的 PRBS 控制。"""

    def __init__(self, host: str, user: str = "root", password: str = "",
                 port: int = 22, slot: int = 1, args=None, name: str = ""):
        self.host = str(host or "").strip()
        self.name = str(name or self.host or "5808-front").strip()
        self.slot = int(slot)
        self.args = args
        self.target = ConnectionTarget(
            name=self.name, host=self.host, port=port, user=user,
            password=password, meta={"kind": "5808_front"})

    def run(self, commands: Sequence[str], enter_diagnose: bool = False) -> List[dict]:
        """执行一批命令；需要时先进入 system-view/diagnose，返回业务命令结果。"""
        setup = list(SETUP_COMMANDS) if enter_diagnose else []
        runner = create_runner(args=self.args, run_log=None,
                               connection_type="ssh_switch")
        results = runner.run([self.target], setup + list(commands))
        if setup:
            for result in results[:len(setup)]:
                if not command_succeeded(result):
                    raise RuntimeError(f"5808 进入诊断视图失败: {_reason(result)}")
        return results[len(setup):]

    def query_ports(self) -> List[SwitchPort]:
        """用户视图查询端口映射；无结果或连接失败时抛 RuntimeError。"""
        results = self.run([PAGINATION_OFF,
                            MAPPING_COMMAND.format(slot=self.slot)])
        mapping = results[-1] if results else {}
        if not mapping.get("ok"):
            raise RuntimeError(f"5808 端口映射查询失败: {_reason(mapping)}")
        output = mapping.get("stdout", "")
        if "more" in output.lower() and "----" in output:
            raise RuntimeError("5808 端口映射输出被分页截断（screen-length 未生效）")
        ports = parse_port_mapping(output)
        if not ports:
            raise RuntimeError("5808 端口映射输出为空或无法解析")
        return ports

    def set_prbs(self, ports: Sequence[SwitchPort], enable: bool) -> List[dict]:
        commands = [set_prbs_command(self.slot, port, enable) for port in ports]
        return self.run(commands, enter_diagnose=True)

    def get_prbs(self, ports: Sequence[SwitchPort]) -> List[dict]:
        commands = [get_prbs_command(self.slot, port) for port in ports]
        return self.run(commands, enter_diagnose=True)


def format_get_report(ports: Sequence[SwitchPort], results: Sequence[dict],
                      switch_name: str = "") -> List[str]:
    """整理 5808 get 结果：每个端口一行汇总 + 每 lane 一行明细。"""
    lines = []
    prefix = f"{switch_name} " if switch_name else ""
    for index, port in enumerate(ports):
        result = results[index] if index < len(results) else {}
        parsed = parse_prbs_output(result.get("stdout", ""))
        if not command_succeeded(result):
            lines.append(f"  {prefix}chip{port.chip} port{port.port} ({port.interface}): "
                         f"失败: {_reason(result)}")
            continue
        if parsed is None:
            lines.append(f"  {prefix}chip{port.chip} port{port.port} ({port.interface}): "
                         f"输出无法解析: {_reason(result)}")
            continue
        lines.append(f"  {prefix}chip{port.chip} port{port.port} ({port.interface}): "
                     f"EN={parsed.en} P={parsed.lanes} errNum={parsed.err_num}")
        for lane in parsed.serdes:
            lines.append(f"    unit{lane['unit']} serdes{lane['serdes']}: "
                         f"en={lane['en']} errNum={lane['err_num']} "
                         f"rate={lane['rate']} alos={lane['alos']}")
    return lines


def _reason(result: dict) -> str:
    text = str(result.get("stderr") or result.get("stdout") or "").strip()
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return f"rc={result.get('rc', '?')}"


def _clean(text: str) -> str:
    return ANSI_RE.sub("", str(text or "")).replace("\r", "")
