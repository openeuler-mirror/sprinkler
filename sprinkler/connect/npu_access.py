"""Helpers for identifying the CPU OS that owns an NPU command."""

import shlex

LOCAL_CPU_HOSTS = {"", "local", "localhost", "127.0.0.1", "::1"}
NPU_SSH_OPTIONS = ("-tt -o BatchMode=yes -o StrictHostKeyChecking=no "
                   "-o UserKnownHostsFile=/dev/null -o LogLevel=ERROR")
#: Same options without ``-tt``: a PTY would corrupt a binary upload on stdin.
NPU_UPLOAD_OPTIONS = ("-o BatchMode=yes -o StrictHostKeyChecking=no "
                      "-o UserKnownHostsFile=/dev/null -o LogLevel=ERROR")

def is_local_cpu_host(host):
    return str(host or "").strip().lower() in LOCAL_CPU_HOSTS

def npu_ssh_argv(execute_eid, command, tty=True):
    """Build an ``ssh`` argv list for callers that stream stdin/stdout."""
    host = str(execute_eid or "").strip()
    if not host:
        raise ValueError("NPU SSH host missing")
    options = NPU_SSH_OPTIONS if tty else NPU_UPLOAD_OPTIONS
    return ["ssh", *shlex.split(options), f"root@{host}", str(command or "")]

def npu_os_command(execute_eid, command):
    host = str(execute_eid or "").strip()
    if not host:
        raise ValueError("NPU SSH host missing")
    return f"ssh {NPU_SSH_OPTIONS} {shlex.quote(f'root@{host}')} {shlex.quote(str(command or ''))}"

def npu_side_command(side, command):
    execute_eid = str((side or {}).get("execute_eid") or "").strip()
    return npu_os_command(execute_eid, command) if execute_eid else str(command or "")
