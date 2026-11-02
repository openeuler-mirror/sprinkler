"""
Kill all processes that are occupying the NPU.
"""

import subprocess
import re
from collections import defaultdict
from typing import List, Dict

from sprinkler.base.log import RunLog


def npu_resource_kill(args) -> int:
    CHECK_ID = "npu_resource_kill"
    run_log = RunLog(args, CHECK_ID)

    run_log.info("开始NPU资源释放：从 npu-smi info 获取占用进程并终止")

    killed = kill_npu_processes(run_log)

    if killed > 0:
        run_log.ok(f"NPU资源释放完成，共终止 {killed} 个进程")
        return 0
    else:
        run_log.info("未找到需要终止的NPU占用进程")
        return 0


def get_npu_processes() -> List[Dict[str, str]]:
    result = subprocess.run(
        ['npu-smi', 'info'],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(f"npu-smi info 执行失败: {result.stderr}")

    lines = result.stdout.splitlines()
    processes = []
    in_process_section = False

    for line in lines:
        stripped = line.strip()

        if 'Process id' in stripped and 'Process name' in stripped:
            in_process_section = True
            continue

        if not in_process_section:
            continue
        if stripped.startswith('+') or not stripped:
            continue

        if 'No running processes' in stripped:
            continue

        match = re.match(r'\|\s*(\d+)\s+\|\s*(\d+)\s+\|\s*(\S+)', stripped)
        if match:
            npu_id, pid, name = match.groups()
            processes.append({
                'npu_id': npu_id,
                'pid': pid,
                'name': name
            })

    return processes


def kill_npu_processes(run_log: RunLog) -> int:
    try:
        processes = get_npu_processes()
    except Exception as e:
        run_log.fail(f"获取NPU进程列表失败: {e}")
        return 0

    if not processes:
        return 0

    seen_pids = set()
    unique_procs = []
    for p in processes:
        if p['pid'] not in seen_pids:
            seen_pids.add(p['pid'])
            unique_procs.append(p)

    name_groups = defaultdict(list)
    for p in unique_procs:
        name_groups[p['name']].append(p)

    total_killed = 0

    for name, procs in name_groups.items():
        run_log.info(f"进程 '{name}'：发现 {len(procs)} 个实例")
        for p in procs:
            try:
                subprocess.run(
                    ['kill', '-9', p['pid']],
                    check=True,
                    capture_output=True
                )
                run_log.info(f"  已终止 PID {p['pid']} (NPU {p['npu_id']})")
                total_killed += 1
            except subprocess.CalledProcessError:
                run_log.info(f"  PID {p['pid']} 终止失败（进程可能已退出）")
            except Exception as e:
                run_log.fail(f"  PID {p['pid']} 终止异常: {e}")

    return total_killed
