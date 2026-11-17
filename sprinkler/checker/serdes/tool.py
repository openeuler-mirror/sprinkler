"""Locate and, when missing, upload ``serdestool.bin`` onto the NPU OS.

The NPU OS is expected to keep the tool at ``/home/serdestool.bin``.  Before a
check runs on an NPU we probe that path; when it is absent we resolve the CPU
OS source path and stream the binary over SSH::

    cat serdestool.bin | ssh root@<npu> "cat > /home/serdestool.bin && chmod +x /home/serdestool.bin"

Source path resolution order:

1. ``sprinkler_config/config.conf`` -> ``SERDES_TOOL_PATH``
2. interactive prompt (the answer is saved back to the config file)
"""

import shlex
import subprocess

from sprinkler.config.config_manager import SerdesConfigManager
from sprinkler.connect.npu_access import npu_os_command, npu_ssh_argv

DEFAULT_NPU_PATH = "/home/serdestool.bin"
CHECK_TIMEOUT = 15
UPLOAD_TIMEOUT = 300


def tool_present(execute_eid, path, timeout=CHECK_TIMEOUT):
    """True when ``path`` exists on the NPU OS."""
    command = f"test -f {shlex.quote(str(path))}"
    try:
        completed = subprocess.run(
            npu_os_command(execute_eid, command), shell=True, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout)
    except Exception:
        return False
    return completed.returncode == 0


def upload_tool(source, execute_eid, target_path, timeout=UPLOAD_TIMEOUT):
    """Stream ``source`` (CPU OS) to ``target_path`` (NPU OS).

    Returns ``(ok, message)``.  ``chmod +x`` is applied remotely because the
    pipe creates a plain file that would otherwise not be executable.
    """
    remote = f"cat > {shlex.quote(str(target_path))} && chmod +x {shlex.quote(str(target_path))}"
    try:
        with open(source, "rb") as handle:
            completed = subprocess.run(
                npu_ssh_argv(execute_eid, remote, tty=False),
                stdin=handle, capture_output=True, timeout=timeout)
    except Exception as exc:
        return False, str(exc)

    if completed.returncode == 0:
        return True, ""
    detail = completed.stderr.decode("utf-8", "replace").strip() if completed.stderr else ""
    return False, detail or f"rc={completed.returncode}"


def resolve_source(run_log=None):
    """Return the CPU OS path of ``serdestool.bin``; empty when unavailable."""
    path = SerdesConfigManager().get_serdes_path()
    if not path:
        _log(run_log, "FAIL", "no CPU OS serdestool path configured; "
                              "set SERDES_TOOL_PATH in sprinkler_config/config.conf")
        return ""
    return path


def ensure_tools(items, npu_path, run_log=None):
    """Ensure ``npu_path`` exists on every NPU, uploading it where needed.

    Items that stay unavailable get ``item["error"]`` set; the caller turns
    those into failure rows instead of aborting the whole run.
    """
    missing = []
    for item in items:
        present = tool_present(item["execute_eid"], npu_path)
        _log(run_log, "INFO",
             f"{item['target']} tool={'ready' if present else 'missing'} path={npu_path}")
        if not present:
            missing.append(item)

    if not missing:
        return items

    source = resolve_source(run_log)
    if not source:
        reason = f"serdestool missing on NPU OS ({npu_path}) and no CPU OS source configured"
        for item in missing:
            item["error"] = reason
        return items

    for item in missing:
        _log(run_log, "INFO", f"{item['target']} upload {source} -> {npu_path}")
        ok, message = upload_tool(source, item["execute_eid"], npu_path)
        if not ok:
            item["error"] = f"serdestool upload failed: {message}"
            _log(run_log, "FAIL", f"{item['target']} upload failed: {message}")
            continue
        if not tool_present(item["execute_eid"], npu_path):
            item["error"] = f"serdestool upload finished but {npu_path} is still missing"
            _log(run_log, "FAIL", f"{item['target']} {item['error']}")
            continue
        item["uploaded"] = True
        _log(run_log, "PASS", f"{item['target']} upload done")

    return items


def _log(run_log, level, message):
    if run_log is not None:
        run_log.log(level, message)
