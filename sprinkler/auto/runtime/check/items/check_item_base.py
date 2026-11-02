import os
import re
from abc import ABC, abstractmethod

from sprinkler.base.log import RunLog


class CheckItemBase(ABC):
    """单项检查基类（纯解析器）。子类实现 get_commands（声明需要的命令）和 analyze_log（解析日志）。"""

    CHECK_NAME: str = ""
    CONNECTION_TYPE: str | None = None
    TARGET_TYPE: str | None = None

    _CMD_LINE_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \$\s*(.+)")
    _NEXT_CMD_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \$")

    def __init__(self, log_path: str = "", run_log: RunLog = None):
        self.log_path = log_path
        self.run_log = run_log
        self.log_content = ""
        self.anomalies: list[str] = []
        self._read_log()

    @staticmethod
    @abstractmethod
    def get_commands(chip_count: int = 0, slot_id: int = 0, **_kwargs) -> list[str]:
        """返回该检查项需要的命令列表。"""

    @abstractmethod
    def analyze_log(self) -> bool:
        """解析日志，返回是否通过。异常写入 self.anomalies。"""

    def _read_log(self):
        if self.log_path and os.path.exists(self.log_path):
            with open(self.log_path, "r", encoding="utf-8") as f:
                self.log_content = f.read()
        if not self.log_content:
            self.anomalies.append("日志文件为空或不存在，无法分析")

    def _extract_command_blocks(self, command_pattern: str) -> dict[str, str]:
        blocks = {}
        lines = self.log_content.split("\n")
        i = 0
        while i < len(lines):
            match = self._CMD_LINE_RE.match(lines[i])
            if match:
                cmd = match.group(1).strip()
                if re.search(command_pattern, cmd):
                    i += 1
                    while i < len(lines) and lines[i].strip().startswith("---"):
                        i += 1
                    if i < len(lines) and lines[i].strip() == "":
                        i += 1
                    block_lines = []
                    while i < len(lines):
                        if (
                            self._NEXT_CMD_RE.match(lines[i])
                            or re.match(r"<[^>]+>", lines[i].strip())
                        ):
                            break
                        block_lines.append(lines[i])
                        i += 1
                    blocks[cmd] = "\n".join(block_lines)
                    continue
            i += 1
        return blocks

    def _get_node_info(self) -> str:
        for line in self.log_content.split("\n"):
            match = re.search(r"target=(\S+)", line)
            if match:
                return match.group(1).strip()
        return ""
