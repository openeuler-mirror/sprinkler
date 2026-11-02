"""
MPirun打流测试功能模块
提供单机和集群模式的mpirun带宽测试功能
"""

from sprinkler.auto.oneclick.mpirun_bandwidth import mpirun_bandwidth_check
from sprinkler.auto.oneclick.mpirun_cluster import mpirun_cluster_check
from sprinkler.auto.oneclick.pta_test import pta_test_check
from sprinkler.auto.oneclick.pta_cluster.run import pta_cluster_check

__all__ = [
    "mpirun_bandwidth_check",
    "mpirun_cluster_check",
    "pta_test_check",
    "pta_cluster_check",
]
