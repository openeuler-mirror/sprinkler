"""Run ping checks from planned URMA EID pairs."""

import re
import shlex
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from sprinkler.auto.bandwidth.planning.eid_pairs import partition_executable_pairs, plan_eid_pairs
from sprinkler.auto.bandwidth.common import command_result, is_xboard_pair, has_link_distinction, link_suffix, compact_target_text, pair_target, pair_context, display_reason, role_eid, role_execute_eid, execution_host, missing_xboard_fields, grouped, XBOARD_PING_REQUIRED_FIELDS
from sprinkler.base.log import RunLog
from sprinkler.base.results import CommandResultView
from sprinkler.connect.connection_runner import create_runner
from sprinkler.connect.npu_access import is_local_cpu_host, npu_os_command
from sprinkler.connect.targets import ConnectionTarget

CHECK_ID = "ping_check"
PING_PROCESS_TIMEOUT = 10
PING_OPTIONS = "-c 3 -W 2 -w 6"
PING_BLOCK_WORKERS = 8

def ping_mode(pair):
    kind = str((pair or {}).get("kind") or "").lower()
    if is_xboard_pair(pair):
        return "ubg" if link_suffix(pair.get("link")) == "l2" else "xd2d"
    return kind.removeprefix("intra_") or "unknown"

def ping_result_text(result):
    parts = [compact_target_text(result) or "ping"]
    link = link_suffix(result.get("link")) if result.get("ping_show_link") else ""
    if link:
        parts.append(f"link={link}")
    return " ".join(parts)

class PingCheckResultView:
    def __init__(self, formatter):
        self.formatter = formatter
        self.command_view = CommandResultView(formatter)

    def log_level(self, result: dict, ok: bool):
        if ok:
            loss_percent = result.get("loss_percent")
            if loss_percent is not None and loss_percent > 0:
                return "WARN"
        return "PASS" if ok else "FAIL"

    def summary(self, result: dict, level: str):
        parts = [ping_result_text(result)]
        reason = display_reason(result.get("reason")) if not self.formatter.result_ok(result) else ""
        if reason:
            parts.append(f"reason={reason}")
        parts.append(f"cmd={result.get('cmd', '')}")
        loss_percent = result.get("loss_percent")
        if loss_percent is not None and loss_percent > 0:
            parts.append(f"丢包率: {loss_percent}%")
        return " ".join(parts)

    def output_blocks(self, result: dict, ok: bool, show_output: bool):
        item = result if result.get("target") else {**result, "target": "ping"}
        return self.command_view.output_blocks(item, ok, show_output)

class PingCheckRunner:
    def __init__(self, args=None, run_log=None):
        self.args = args
        self.run_log = run_log
        self.ssh_runner = create_runner(args=args, run_log=None, connection_type="ssh_os")

    def run(self, pairs, on_result=None):
        results = [None] * len(pairs)
        show_link = has_link_distinction(pairs)
        callback_lock = Lock()
        blocks = grouped(list(enumerate(pairs)), lambda item: ping_mode(item[1]))
        for mode, block in blocks.items():
            order = [index for index, _pair in block]
            pending = {}
            next_position = 0
            def emit(index, result):
                nonlocal next_position
                result["ping_show_link"] = show_link
                with callback_lock:
                    results[index] = result
                    pending[index] = result
                    while next_position < len(order) and order[next_position] in pending:
                        ready = pending.pop(order[next_position])
                        next_position += 1
                        if on_result is not None:
                            on_result(ready)

            if self.run_log is not None:
                self.run_log.info(f"ping_block=started mode={mode} total={len(block)}")
            lanes = list(grouped(block, lambda item: self.lane_key(*item)).values())
            if len(lanes) == 1:
                self.run_lane(lanes[0], emit)
            elif lanes:
                with ThreadPoolExecutor(max_workers=min(PING_BLOCK_WORKERS, len(lanes))) as pool:
                    list(pool.map(lambda lane: self.run_lane(lane, emit), lanes))
            for index, pair in block:
                if results[index] is None:
                    emit(index, self.fail_result(pair, "runner", "ping result missing"))
            if self.run_log is not None:
                passed = sum(bool(results[index] and results[index].get("ok")) for index, _pair in block)
                self.run_log.info(f"ping_block=finished mode={mode} passed={passed} total={len(block)}")
        final_results = list(results)
        if self.run_log is not None:
            for result in final_results:
                self.run_log.write_target_results(result.get("target") or "ping", [result])
        return final_results

    def lane_key(self, index, pair):
        if pair.get("runner") == "host":
            return "host", index
        if pair.get("runner") != "npu":
            return "config", index
        host = str(pair.get("client_cpu_host") or "").strip() if is_xboard_pair(pair) else "local"
        host = host or "local"
        return (("npu", role_execute_eid(pair, "client") or index) if is_local_cpu_host(host)
                else ("remote", host))

    def run_lane(self, items, emit):
        emitted = set()
        def lane_emit(index, result):
            emitted.add(index)
            emit(index, result)

        try:
            remote = []
            for index, pair in items:
                missing = missing_xboard_fields(pair, XBOARD_PING_REQUIRED_FIELDS)
                if missing:
                    result = self.fail_result(pair, "config", f"xboard pair missing {missing[0]}")
                elif not pair.get("ok"):
                    result = self.fail_result(pair, "config", pair.get("reason") or "bad eid pair")
                elif pair.get("runner") == "host":
                    result = self.run_local(pair)
                elif pair.get("runner") == "npu":
                    if not role_eid(pair, "client") or not role_eid(pair, "server"):
                        result = self.fail_result(pair, "config", "eid missing")
                    elif not role_execute_eid(pair, "client"):
                        result = self.fail_result(pair, "config", "client_execute_eid missing")
                    else:
                        host = (str(pair.get("client_cpu_host") or "").strip()
                                if is_xboard_pair(pair) else "local") or "local"
                        command = npu_os_command(role_execute_eid(pair, "client"), self.command(pair))
                        if not is_local_cpu_host(host):
                            remote.append({"index": index, "pair": pair, "host": host, "command": command})
                            continue
                        result = self.run_local(pair, command)
                else:
                    result = self.fail_result(pair, "runner", f"unsupported runner: {pair.get('runner')}")
                lane_emit(index, result)

            if remote:
                self.run_npu_group(remote, lane_emit)
        except Exception as exc:
            for index, pair in items:
                if index not in emitted:
                    lane_emit(index, self.fail_result(pair, "runner", str(exc)))

    def command(self, pair):
        eid_dest = role_eid(pair, "server")
        if eid_dest:
            # 正常EID路径: IPv6 ping, 指定源地址
            src = shlex.quote(role_eid(pair, "client"))
            return f"ping6 {PING_OPTIONS} {shlex.quote(eid_dest)} -I {src}"
        # EID不可用但配置有静态IPv4, 用IPv4 ping, 不指定源IP
        ipv4 = str((pair or {}).get("server_ipv4") or "").strip()
        if ipv4:
            return f"ping {PING_OPTIONS} {shlex.quote(ipv4)}"
        src = shlex.quote(role_eid(pair, "client"))
        return f"ping6 {PING_OPTIONS} {shlex.quote(eid_dest)} -I {src}"

    def run_local(self, pair, command=None):
        if not role_eid(pair, "client") or not role_eid(pair, "server"):
            if not str((pair or {}).get("server_ipv4") or "").strip():
                return self.fail_result(pair, "config", "eid missing")
        command = command or self.command(pair)
        start = time.time()
        try:
            completed = subprocess.run(command, shell=True, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", timeout=PING_PROCESS_TIMEOUT)
            rc = completed.returncode
            loss_percent = None
            match = re.search(r'(\d+)% packet loss', completed.stdout or "")
            if match:
                loss_percent = int(match.group(1))
            ok = completed.returncode == 0 and loss_percent == 0
            if not ok and completed.returncode in (0, 1) and loss_percent is not None and loss_percent < 100:
                ok = True
            error_type = "" if ok else "ping"
            stdout = completed.stdout
            stderr = completed.stderr
        except subprocess.TimeoutExpired as exc:
            ok = False
            rc = 255
            error_type = "timeout"
            stdout = exc.stdout or ""
            stderr = exc.stderr or str(exc)
        raw = command_result(target=pair_target(pair), host=execution_host(pair), cmd=command, ok=ok, rc=rc,
                             error_type=error_type, stdout=stdout, stderr=stderr, elapsed=time.time() - start,
                             backend="local", loss_percent=loss_percent)
        return self.result(pair, raw)

    def run_npu_group(self, items, on_result=None):
        first = items[0]["pair"]
        host = items[0]["host"]
        meta = {"kind": "npu_os", "rank": first.get("client_rank"), "subsystem": "npu_os"}
        target = ConnectionTarget(name=str(first.get("client_target") or host), host=host, meta=meta)
        commands = [item["command"] for item in items]
        converted = []
        def handle_result(result):
            if len(converted) >= len(items):
                return
            item = items[len(converted)]
            converted.append((item["index"], self.result(item["pair"], result)))
            if on_result is not None:
                on_result(*converted[-1])

        results = self.ssh_runner.run([target], commands, on_result=handle_result)
        for item, result in zip(items[len(converted):], results[len(converted):]):
            converted.append((item["index"], self.result(item["pair"], result)))
            if on_result is not None:
                on_result(*converted[-1])
        return converted

    def fail_result(self, pair, error_type, reason):
        has_eids = role_eid(pair, "client") and role_eid(pair, "server")
        command = self.command(pair) if has_eids else "ping check"
        raw = command_result(target=pair_target(pair), host=execution_host(pair), cmd=command, ok=False, rc=255,
                             error_type=error_type, stderr=reason, backend="planner")
        return self.result(pair, raw)

    def result(self, pair, result):
        result = dict(result)
        source = role_eid(pair, "client")
        destination = role_eid(pair, "server")
        ipv4 = str((pair or {}).get("server_ipv4") or "").strip()
        is_ipv4 = not destination and bool(ipv4)
        if is_ipv4:
            destination = ipv4
            source = ""
        result["target"] = pair_target(pair)
        result["host"] = execution_host(pair)
        result["cmd"] = self.command(pair) if (source and destination) or ipv4 else result.get("cmd") or "ping check"
        result.update(pair_context(pair),
                      ping_family="ipv4" if is_ipv4 else "ipv6",
                      ping_host=destination)
        reason = result.get("stderr") or result.get("error_type") or "ping failed"
        result["reason"] = "ping ok" if result.get("ok") else reason
        return result

class PingPrecheck:
    def __init__(self, args=None, run_log=None, runner_cls=PingCheckRunner):
        self.args = args
        self.run_log = run_log
        self.runner_cls = runner_cls

    def run(self, pairs, on_result=None):
        if self.run_log is not None:
            log_ping_start(self.run_log, pairs)
        result_view = PingCheckResultView(self.run_log.formatter) if self.run_log is not None else None
        def _on_result(result):
            if self.run_log is not None:
                self.run_log._log_result(result, result_view)
            if on_result is not None:
                on_result(result)

        ping_results = self.runner_cls(args=self.args, run_log=self.run_log).run(pairs, on_result=_on_result)
        passed_pairs = []
        failed_results = []
        for pair, result in zip(pairs, ping_results):
            ok = self.run_log.formatter.result_ok(result) if self.run_log is not None else bool(result.get("ok"))
            if ok:
                passed_pairs.append(pair)
            else:
                failed_results.append(result)
        if self.run_log is not None:
            if failed_results:
                targets = ",".join(ping_result_text(result) for result in failed_results[:10])
                suffix = f",+{len(failed_results) - 10}" if len(failed_results) > 10 else ""
                message = f"ping_check=failed failed={len(failed_results)} failed_targets={targets}{suffix}"
                log_method = getattr(self.run_log, "log", None)
                if log_method:
                    log_method("WARN", message)
                else:
                    self.run_log.info(message)
            if passed_pairs:
                self.run_log.info(f"ping_check=passed total={len(passed_pairs)}")
        return passed_pairs, failed_results

def log_ping_start(run_log, pairs):
    run_log.info(f"ping_check=started total={len(pairs)}")
    seen = set()
    for pair in pairs:
        key = (str(pair.get("kind") or "ping"), execution_host(pair))
        if key in seen:
            continue
        seen.add(key)
        run_log.info(f"target={key[0]} host={key[1]}")

def ping_check_execute(args):
    run_log = RunLog(args, default_check_id=CHECK_ID)
    run_log.info(f"log_dir={run_log.path}")
    result_view = PingCheckResultView(run_log.formatter)

    def on_result(result):
        run_log._log_result(result, result_view)

    try:
        _, ranks, pairs = plan_eid_pairs(args, run_log)
        pairs, skipped = partition_executable_pairs(pairs)
        run_log.info(f"rank={','.join(str(rank) for rank in ranks)} pair_total={len(pairs)} "
                     f"skipped_no_topo_mapping={skipped}")
        log_ping_start(run_log, pairs)
        results = PingCheckRunner(args=args, run_log=run_log).run(pairs, on_result=on_result)
    except Exception as exc:
        return run_log.fail_error(CHECK_ID, exc)

    run_log.write_results(results)
    passed = sum(1 for result in results if run_log.formatter.result_ok(result))
    total = len(results)
    run_log.auto_details(CHECK_ID, passed, total)
    run_log.finish_log_dir()
    return 0 if total > 0 and passed == total else 1
