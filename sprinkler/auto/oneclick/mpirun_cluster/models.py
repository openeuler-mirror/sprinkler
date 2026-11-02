"""
MPirun集群测试数据模型
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ClusterConfig:
    """集群配置"""
    cluster_conf: str
    mpich_path: str
    cann_path: str
    timeout_seconds: int = 180


@dataclass
class CabinetTestResult:
    """单个柜子测试结果"""
    cabinet_name: str
    success: bool
    bandwidth: Optional[float]
    log_file: Path
