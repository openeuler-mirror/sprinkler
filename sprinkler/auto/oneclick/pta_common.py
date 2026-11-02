"""
PTA测试公共模块
单机/集群测试共享的工具函数、常量和数据结构
"""

import os
import re
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sprinkler.base.log import RunLog
from sprinkler.config.config_manager import PtaConfigManager
from sprinkler.logging import Colors, output_auto_details

TEST_SCRIPT_MAP = {
    "allreduce": "test_allreduce.py",
    "alltoall": "test_all2all.py",
    "gpt": "test_gpt.py",
}

VALID_TEST_TYPES = tuple(TEST_SCRIPT_MAP.keys())

@dataclass
class RankResult:
    success_ranks: list[int] = field(default_factory=list)
    failed_ranks: list[int] = field(default_factory=list)
    device_set_count: int = 0
    init_count: int = 0

class OutputParser:
    DEVICE_DONE_PATTERN = re.compile(r"npu:(\d+).*set device done")
    INIT_SUCCESS_PATTERN = re.compile(r"Rank\s+(\d+)\s+成功初始化进程组")
    EXEC_FAIL_PATTERN = re.compile(r"Rank\s+(\d+)\s+执行失败")
    GPT_DEVICE_PATTERN = re.compile(r"Device:")
    ALLTOALL_TENSOR_PATTERN = re.compile(r"tensor\(\[.*\].*device='npu:(\d+)")
    ALLREDUCE_VALUE_PATTERN = re.compile(r"Hccl:.*tensor\(\[([0-9.]+)\]")

    def parse_line(self, line: str, test_type: str, result: RankResult) -> Optional[str]:
        m = self.DEVICE_DONE_PATTERN.search(line)
        if m:
            result.device_set_count += 1
            return f"  ✓ Device {m.group(1)} 设置成功"

        m = self.INIT_SUCCESS_PATTERN.search(line)
        if m:
            rank_num = int(m.group(1))
            result.success_ranks.append(rank_num)
            return f"  ✓ Rank {rank_num} 初始化成功"

        m = self.EXEC_FAIL_PATTERN.search(line)
        if m:
            rank_num = int(m.group(1))
            result.failed_ranks.append(rank_num)
            return f"  ✗ Rank {rank_num} 执行失败"

        if test_type == "gpt" and self.GPT_DEVICE_PATTERN.search(line):
            return f"  {line.strip()}"

        if test_type == "alltoall":
            m = self.ALLTOALL_TENSOR_PATTERN.search(line)
            if m:
                device_num = m.group(1)
                values_m = re.search(r"tensor\(\[([^\]]+)\]", line)
                tensor_values = values_m.group(1) if values_m else ""
                return f"  Device {device_num}: {tensor_values}"

        if test_type == "allreduce":
            m = self.ALLREDUCE_VALUE_PATTERN.search(line)
            if m:
                return f"  结果值: {m.group(1)}"

        return None

def get_local_ip() -> str:
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return ""
    finally:
        if s:
            s.close()

def get_local_ips() -> list[str]:
    try:
        hostname = socket.gethostname()
        return list(set(
            addr[4][0] for addr in socket.getaddrinfo(hostname, None)
            if addr[0] == socket.AF_INET and addr[4][0] != "127.0.0.1"
        ))
    except Exception:
        return []

def detect_npu_device_count() -> int:
    try:
        import torch
        import torch_npu
        return torch.npu.device_count()
    except Exception:
        return 0


def source_cann_env() -> None:
    config_manager = PtaConfigManager()
    env_path = config_manager.get_env_path()

    if not env_path or not os.path.isfile(env_path):
        return

    print(f"[环境] 使能环境: source {env_path}")
    try:
        result = subprocess.run(
            ["/bin/bash", "-c", f"source {env_path} && env"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "=" in line:
                    key, _, value = line.partition("=")
                    os.environ[key] = value
        else:
            print(f"[警告] CANN环境使能失败: {cann_script}")
    except Exception:
        print(f"[警告] CANN环境使能失败: {cann_script}")

def resolve_test_script(test_type: str, script_path: Optional[str] = None) -> Path:
    if test_type not in TEST_SCRIPT_MAP:
        raise ValueError(f"不支持的测试类型: {test_type}")
    script_name = TEST_SCRIPT_MAP[test_type]
    if script_path:
        p = Path(script_path)
    else:
        p = Path(__file__).resolve().parent.parent.parent / "test" / script_name

    if not p.exists():
        raise FileNotFoundError(f"测试脚本不存在: {p}")
    return p

def parse_progress_from_log(content: str) -> tuple[int, int]:
    device_set = len(re.findall(r"set device done", content))
    init_count = len(re.findall(r"成功初始化进程组", content))
    return device_set, init_count

def validate_results(test_type: str, log_file: Path, expected_count: int) -> Optional[str]:
    if not log_file.exists():
        return "日志文件不存在"

    content = log_file.read_text(encoding="utf-8", errors="replace")

    if test_type == "alltoall":
        tensor_lines = re.findall(r"tensor\(\[([0-9.,\s]+)\]", content)
        if not tensor_lines:
            return "未检测到tensor输出"
        incorrect = sum(
            1 for vals in tensor_lines
            if len([v.strip() for v in vals.split(",") if v.strip()]) != expected_count
        )
        if incorrect > 0:
            correct = len(tensor_lines) - incorrect
            return f"tensor值不正确 (期望{expected_count}个值, 实际{correct}/{len(tensor_lines)}个正确)"
        return None

    if test_type == "allreduce":
        values = re.findall(r"tensor\(\[([0-9.]+)", content)
        if not values:
            return "未检测到tensor结果输出"
        for v in values:
            v_clean = v.rstrip('0').rstrip('.')
            expected_str = str(expected_count)
            if v_clean == expected_str or v == expected_str or v == f"{expected_count}.0":
                return None
        return f"结果值不匹配 (期望{expected_count}, 实际: {values})"

    return None

def check_torchrun() -> None:
    try:
        subprocess.run(
            [sys.executable, "-m", "torch.distributed.run", "--help"],
            capture_output=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        raise RuntimeError("torchrun命令未找到")

def build_pta_config(args) -> "PtaClusterConfig":
    from sprinkler.auto.oneclick.pta_cluster.models import PtaClusterConfig
    return PtaClusterConfig(
        test_type=getattr(args, "op_type", "alltoall"),
        master_port=int(getattr(args, "master_port", 20022)),
        timeout=int(getattr(args, "timeout", 60)),
        nproc_per_node=int(getattr(args, "nproc_per_node", 0)) or None,
        script_path=getattr(args, "script_path", None),
    )
