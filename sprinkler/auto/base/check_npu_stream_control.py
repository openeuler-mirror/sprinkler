"""Check NPU stream control register value via devmem on local and remote NPUs."""

import subprocess
import time

from sprinkler.auto.bandwidth.common import *
from sprinkler.auto.bandwidth.common.route_conf import RouteConf
from sprinkler.base.log import RunLog
from sprinkler.base.results import CommandResultView
from sprinkler.config.host_device_pairs import get_host_and_device_pairs
from sprinkler.connect.connection_runner import create_runner
from sprinkler.connect.npu_access import is_local_cpu_host, npu_os_command
from sprinkler.connect.targets import ConnectionTarget

CHECK_ID = "check_npu_stream_control"
COMMAND_TIMEOUT = 10

REGISTER_MAP = {
    "udie0": ("0x24804099108", "0x248040a1084"),
    "udie1": ("0x2c804099108", "0x2c8040a1084"),
}

def check_stream_control_status(val0, val1):
    def normalize_hex(s):
        s = (s or "").strip().lower()
        if s.startswith("0x") or s.startswith("0x"):
            try:
                return int(s, 16)
            except ValueError:
                return s
        return s
    v0 = normalize_hex(val0)
    v1 = normalize_hex(val1)
    if v0 == 0 and v1 == 1:
        return ("ON", True, False)
    if v0 == 1 and v1 == 0:
        return ("OFF", True, False)
    return (f"{val0},{val1}", False, True)

class NpuStreamControlResultView:
    def __init__(self, formatter):
        self.formatter = formatter
        self.command_view = CommandResultView(formatter)

    def summary(self, result, level):
        target = result.get("target", "npu")
        status = result.get("stream_status", "")
        ok = self.formatter.result_ok(result)
        if ok:
            if "ON" in status or "OFF" in status:
                return f"{target} stream_control={status}"
            else:
                raw = result.get("stdout", "")
                udie0_vals = self._extract_udie_values(raw, "udie0")
                udie1_vals = self._extract_udie_values(raw, "udie1")
                return f"{target} stream_control=udie0={udie0_vals}; udie1={udie1_vals}"
        reason = result.get("stderr") or result.get("error_type") or "devmem failed"
        return f"{target} reason={reason}"

    def _extract_udie_values(self, raw_output, udie_name):
        import re
        pattern = rf"{udie_name}:.*?devmem \S+?=(\S+),.*?devmem \S+?=(\S+)"
        match = re.search(pattern, raw_output)
        if match:
            return f"{match.group(1)},{match.group(2)}"
        return "unknown"

    def output_blocks(self, result, ok, show_output):
        item = result if result.get("target") else {**result, "target": "npu"}
        return self.command_view.output_blocks(item, ok, show_output)

class NpuStreamControlRunner:
    def __init__(self, args=None, run_log=None):
        self.args = args
        self.run_log = run_log
        self.ssh_runner = create_runner(args=args, run_log=None, connection_type="ssh_os")

    def run(self, npu_items, on_result=None):
        results = []
        local_items = []
        remote_groups = {}

        for item in npu_items:
            host = item.get("cpu_host") or "local"
            if is_local_cpu_host(host):
                local_items.append(item)
            else:
                remote_groups.setdefault(host, []).append(item)

        for item in local_items:
            result = self.run_local(item)
            results.append(result)
            if on_result is not None:
                on_result(result)

        for host, items in remote_groups.items():
            group_results = self.run_remote_group(host, items)
            for result in group_results:
                results.append(result)
                if on_result is not None:
                    on_result(result)

        return results

    def run_local(self, item):
        execute_eid = item.get("execute_eid")
        rank = item.get("rank")
        target = item.get("target", f"NPU{rank}")

        if not execute_eid:
            return command_result(target=target, host="local", cmd="stream_control_check",
                                  ok=False, rc=2, error_type="config", stderr="execute_eid missing",
                                  rank=rank, execute_eid="", stream_status="")

        all_ok = True
        stream_status = ""
        combined_stdout = ""
        combined_stderr = ""

        for udie_name, (reg0, reg1) in REGISTER_MAP.items():
            cmd0 = f"devmem {reg0}"
            cmd1 = f"devmem {reg1}"
            val0, val1, ok0, ok1 = self._run_devmem_pair(execute_eid, cmd0, cmd1)
            combined_stdout += f"{udie_name}: {cmd0}={val0}, {cmd1}={val1}\n"
            status, status_ok, _ = check_stream_control_status(val0, val1)
            if not status_ok:
                all_ok = False
            stream_status += f"{udie_name}={status}; "

        return command_result(target=target, host="local", cmd="stream_control_check",
                              ok=all_ok, rc=0, error_type="",
                              stdout=combined_stdout.strip(), stderr=combined_stderr,
                              stream_status=stream_status.strip(),
                              elapsed=0, backend="local",
                              rank=rank, execute_eid=execute_eid)

    def _run_devmem_pair(self, execute_eid, cmd0, cmd1):
        val0, ok0 = self._run_single_devmem(execute_eid, cmd0)
        val1, ok1 = self._run_single_devmem(execute_eid, cmd1)
        return val0, val1, ok0, ok1

    def _run_single_devmem(self, execute_eid, command):
        cmd = npu_os_command(execute_eid, command)
        try:
            completed = subprocess.run(cmd, shell=True, stdin=subprocess.DEVNULL,
                                       capture_output=True, text=True,
                                       encoding="utf-8", errors="replace",
                                       timeout=COMMAND_TIMEOUT)
            if completed.returncode == 0:
                return completed.stdout.strip(), True
            return completed.stderr.strip(), False
        except subprocess.TimeoutExpired:
            return "timeout", False
        except Exception as e:
            return str(e), False

    def run_remote_group(self, host, items):
        first = items[0]
        meta = {"kind": "npu_os", "rank": first.get("rank"), "subsystem": "npu_os"}
        target = ConnectionTarget(name=str(first.get("target") or host), host=host, meta=meta)

        results = []
        for item in items:
            execute_eid = item.get("execute_eid")
            rank = item.get("rank")
            item_target = item.get("target", f"NPU{rank}")

            all_ok = True
            stream_status = ""
            combined_stdout = ""

            for udie_name, (reg0, reg1) in REGISTER_MAP.items():
                cmd0 = npu_os_command(execute_eid, f"devmem {reg0}")
                cmd1 = npu_os_command(execute_eid, f"devmem {reg1}")
                raw_results = self.ssh_runner.run([target], [cmd0, cmd1])
                val0 = raw_results[0].get("stdout", "").strip() if raw_results else ""
                val1 = raw_results[1].get("stdout", "").strip() if len(raw_results) > 1 else ""
                combined_stdout += f"{udie_name}: devmem {reg0}={val0}, devmem {reg1}={val1}\n"
                status, status_ok, _ = check_stream_control_status(val0, val1)
                if not status_ok:
                    all_ok = False
                stream_status += f"{udie_name}={status}; "

            raw = {
                "target": item_target,
                "rank": rank,
                "execute_eid": execute_eid,
                "cmd": "stream_control_check",
                "ok": all_ok,
                "rc": 0,
                "error_type": "",
                "stdout": combined_stdout.strip(),
                "stderr": "",
                "stream_status": stream_status.strip(),
            }
            results.append(raw)
        return results

def build_local_npu_items(args, run_log=None):
    host_pairs = get_host_and_device_pairs()
    rank_spec = str(getattr(args, "rank", "") or "").strip()
    from sprinkler.base.ranges import parse_range
    selected_ranks = parse_range(rank_spec) if rank_spec else sorted(host_pairs.keys())

    items = []
    for rank in selected_ranks:
        values = host_pairs.get(rank)
        if not values:
            if run_log:
                run_log.info(f"rank={rank} skipped: no host_device_pair")
            continue
        npu_eid = values[1] if len(values) > 1 else ""
        npu_eid = RouteConf.format_eid32(npu_eid) if npu_eid else ""
        if not npu_eid:
            if run_log:
                run_log.info(f"rank={rank} skipped: npu_eid missing")
            continue
        items.append({
            "rank": rank,
            "target": f"NPU{rank}",
            "execute_eid": npu_eid,
            "cpu_host": "local",
        })
    return items

def build_remote_npu_items(args, run_log=None):
    if getattr(args, "mode", "") != "xd2d":
        return []

    from sprinkler.auto.bandwidth.planning.eid_pairs import plan_eid_pairs
    from sprinkler.base.ranges import parse_range

    try:
        _, ranks, pairs = plan_eid_pairs(args, run_log)
    except Exception:
        if run_log:
            run_log.info("remote_npu: no xboard pairs available, skipping remote NPUs")
        return []

    seen = {}
    for pair in pairs:
        if not pair.get("ok", True):
            continue
        for role in ("client", "server"):
            rank = pair.get(f"{role}_rank")
            execute_eid = role_execute_eid(pair, role)
            target = pair.get(f"{role}_target", f"NPU{rank}")
            cpu_host = str(pair.get(f"{role}_cpu_host") or "").strip() if is_xboard_pair(pair) else "local"
            cpu_host = cpu_host or "local"
            if rank not in seen and execute_eid:
                seen[rank] = {
                    "rank": rank,
                    "target": target,
                    "execute_eid": execute_eid,
                    "cpu_host": cpu_host,
                }
    return list(seen.values())

def check_npu_stream_control_execute(args):
    run_log = RunLog(args, default_check_id=CHECK_ID)
    run_log.info(f"log_dir={run_log.path}")
    result_view = NpuStreamControlResultView(run_log.formatter)

    def on_result(result):
        run_log._log_result(result, result_view)

    try:
        local_items = build_local_npu_items(args, run_log)
        run_log.info(f"local_npu_total={len(local_items)}")

        remote_items = build_remote_npu_items(args, run_log)
        run_log.info(f"remote_npu_total={len(remote_items)}")

        npu_items = local_items + remote_items
        run_log.info(f"npu_total={len(npu_items)}")

        if not npu_items:
            run_log.info("no NPU items found, nothing to check")
            run_log.write_results([])
            run_log.auto_details(CHECK_ID, 0, 0)
            run_log.finish_log_dir()
            return 0

        runner = NpuStreamControlRunner(args=args, run_log=run_log)
        results = runner.run(npu_items, on_result=on_result)
    except Exception as exc:
        return run_log.fail_error(CHECK_ID, exc)

    run_log.write_results(results)
    passed = sum(1 for result in results if run_log.formatter.result_ok(result))
    total = len(results)
    run_log.auto_details(CHECK_ID, passed, total)
    run_log.finish_log_dir()
    return 0 if total > 0 and passed == total else 1