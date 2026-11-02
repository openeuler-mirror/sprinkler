"""
PTA集群测试数据模型
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sprinkler.connect.targets import ConnectionTarget

@dataclass
class PtaClusterConfig:
    test_type: str = "allreduce"
    master_port: int = 20022
    timeout: int = 60
    nproc_per_node: Optional[int] = None
    script_path: Optional[str] = None

@dataclass
class PtaNodeInfo:
    target: ConnectionTarget
    node_rank: int
    nproc: int
    is_local: bool = False

@dataclass
class PtaNodeResult:
    node_name: str
    host: str
    success: bool
    log_file: Optional[Path] = None
    error: str = ""
