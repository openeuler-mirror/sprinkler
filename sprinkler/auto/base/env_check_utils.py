"""命令执行工具：本地 subprocess 与远程 runner 封装"""

import subprocess


def run_local(command: str, cwd: str = None) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, cwd=cwd
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as exc:
        return -1, "", str(exc)


def run_remote(runner, target, command: str) -> dict:
    results = runner.run(targets=[target], commands=[command])
    return results[0] if results else {}