import re
import subprocess
from typing import Dict, Optional, Tuple

from sprinkler.base.log import RunLog


def npu_resource_check(args) -> int:
    CHECK_ID = "npu_resource_check"
    run_log = RunLog(args, CHECK_ID)

    run_log.info("开始NPU资源状态检查")

    try:
        result = subprocess.run(
            ['npu-smi', 'info'],
            capture_output=True,
            text=True,
            timeout=30
        )
        output = result.stdout
        if not output:
            run_log.fail("npu-smi info 无输出")
            return 1

    except subprocess.TimeoutExpired:
        run_log.fail("npu-smi info 执行超时")
        return 1
    except FileNotFoundError:
        run_log.fail("npu-smi 命令未找到，请检查环境")
        return 1
    except Exception as e:
        run_log.fail(f"执行npu-smi info失败: {e}")
        return 1

    device_usage = parse_npu_smi_output(output)

    if not device_usage:
        run_log.info("未解析到NPU设备信息")
        run_log.info("原始输出:")
        for line in output.splitlines()[:20]:
            run_log.info(f"  {line}")
        return 1

    run_log.info("NPU Resource Status:")
    for device_id, (hbm_used_mb, hbm_total_mb, hugepages_used, hugepages_total) in sorted(device_usage.items()):
        hbm_used_gb = hbm_used_mb / 1024.0
        hbm_total_gb = hbm_total_mb / 1024.0
        run_log.info(
            f"  NPU {device_id}: "
            f"HBM-Usage={hbm_used_mb}/{hbm_total_mb} MB ({hbm_used_gb:.2f}/{hbm_total_gb:.2f} GB), "
            f"Hugepages-Usage={hugepages_used}/{hugepages_total} pages"
        )

    run_log.ok("NPU资源状态检查完成")
    return 0


def parse_npu_smi_output(output: str) -> Dict[int, Tuple[int, int, int, int]]:
    device_usage = {}
    lines = output.splitlines()
    
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        
        match = re.match(r'^\|\s*(\d+)\s*\|', line)
        if match:
            device_id = int(match.group(1))
            
            hugepages = _extract_last_ratio(line)
            
            hbm = None
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                if re.match(r'^\|\s*\|', next_line):
                    hbm = _extract_last_ratio(next_line)
                    i += 1
            
            if hbm is not None and hugepages is not None:
                device_usage[device_id] = (hbm[0], hbm[1], hugepages[0], hugepages[1])
        
        i += 1
    
    return device_usage


def _extract_last_ratio(line: str) -> Optional[Tuple[int, int]]:
    parts = line.split('|')
    last_field = ""
    for part in reversed(parts):
        stripped = part.strip()
        if stripped:
            last_field = stripped
            break
    
    if not last_field:
        return None
    
    matches = re.findall(r'(\d+)\s*/\s*(\d+)', last_field)
    if matches:
        used, total = matches[-1]
        return (int(used), int(total))
    
    return None
