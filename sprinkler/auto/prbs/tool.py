"""serdestool.bin 就绪检查。

PRBS 步骤 a 要求每张 NPU 的 ``/root/serdestool.bin`` 存在且可执行：

- 已存在：跳过下发，但仍执行 ``chmod +x``（与实现思路中的循环命令一致）
- 缺失：由上游 ``ensure_tools`` 通过 ``cat`` 从 CPU OS 下发

源路径解析：

- 优先读取 ``sprinkler_config/config.conf`` 的 ``SERDES_TOOL_PATH``；
- 未配置时交互式询问 CPU OS 上的路径，输入有效文件后自动写回配置；
- 用户直接回车（不输入）则停止 PRBS 流程。
"""

from __future__ import annotations

import shlex
import subprocess
from typing import List

from sprinkler.checker.serdes.tool import ensure_tools
from sprinkler.connect.npu_access import npu_os_command
from sprinkler.logging import Colors

REMOTE_TOOL_PATH = "/root/serdestool.bin"
CHMOD_TIMEOUT = 15


def mark_executable(item: dict) -> str:
    """确保远端 serdestool.bin 可执行，返回错误信息（空串表示成功）。"""
    command = (f"chmod +x {shlex.quote(REMOTE_TOOL_PATH)} && "
               f"test -x {shlex.quote(REMOTE_TOOL_PATH)}")
    try:
        done = subprocess.run(
            npu_os_command(item.get("execute_eid", ""), command), shell=True,
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=CHMOD_TIMEOUT)
    except Exception as exc:
        return f"chmod 失败: {exc}"
    if done.returncode == 0:
        return ""
    detail = _last_line(done.stderr) or _last_line(done.stdout)
    return f"chmod 失败: {detail or 'rc=' + str(done.returncode)}"


def ensure_serdestool(items: List[dict]) -> bool:
    """确保每张卡上的 serdestool.bin 就绪，打印逐卡结果并返回是否全部成功。"""
    if not items:
        print(f"{Colors.RED}没有可用的 NPU 目标，无法检查 serdestool.bin{Colors.RESET}")
        return False

    ensure_tools(items, REMOTE_TOOL_PATH, None)

    failed = []
    for item in items:
        target = item.get("target") or f"NPU{item.get('rank')}"
        error = str(item.get("error") or "").strip()
        if error:
            failed.append(target)
            print(f"  {target}: 失败: {error}")
            continue
        error = mark_executable(item)
        if error:
            failed.append(target)
            print(f"  {target}: {error}")
            continue
        if item.get("uploaded"):
            print(f"  {target}: 已下发 {REMOTE_TOOL_PATH}")
        else:
            print(f"  {target}: {REMOTE_TOOL_PATH} 已存在")

    if failed:
        print(f"{Colors.RED}serdestool.bin 检查失败: {len(failed)}/{len(items)} 张卡异常"
              f"{Colors.RESET}")
        if any("no CPU OS source configured" in str(item.get("error", ""))
               for item in items):
            print(f"{Colors.YELLOW}未提供 CPU OS 上的 serdestool.bin，已停止。"
                  f"请重新运行并在交互提示中输入路径，或在 sprinkler_config/"
                  f"config.conf 中配置 SERDES_TOOL_PATH{Colors.RESET}")
        return False
    print(f"{Colors.GREEN}serdestool.bin 检查通过: {len(items)} 张卡均已就绪{Colors.RESET}")
    return True


def _last_line(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""
