"""HIXL communication benchmark orchestration.

Provides sprinkler command wrappers around HIXL's run_comm_benchmark.py script.
Currently implements:
  - hixl_bandwidth: single-sided communication benchmark (hixl_comm_bench)

Usage from sprinkler CLI:
  sprinkler -hixl-bandwidth -hixl-mode D2rD -rank 4,5
"""

from sprinkler.auto.bandwidth.hixl.runner import hixl_exec, run_comm_bench_exec

__all__ = ["hixl_exec", "run_comm_bench_exec"]
