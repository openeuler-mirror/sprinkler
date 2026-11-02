"""环境检查公共数据结构"""

from dataclasses import dataclass, field


@dataclass
class CheckResult:
    """单节点单项检查结果"""
    name: str
    status: bool
    message: str


@dataclass
class NodeCheckResult:
    """单节点全部检查结果"""
    node: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(c.status for c in self.checks)