"""URMA traffic orchestration for bandwidth measurement."""

import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, ROUND_HALF_UP

from sprinkler.auto.bandwidth.urma.results import traffic_pair_result, fail_pair, empty_process_result, machine_pair, has_link_distinction, display_link, args_link_mode, DEFAULT_SAMPLE_TAIL, DEFAULT_START_STAGGER_SECONDS, DEFAULT_LISTENER_READY_SECONDS
from sprinkler.auto.bandwidth.urma.commands import UrmaCommandBuilder
from sprinkler.auto.bandwidth.urma.executor import UrmaProcessSession, UrmaStreamLog, UrmaCommandExecutor
from sprinkler.auto.bandwidth.urma.show_resolver import UrmaShowResolver
from sprinkler.auto.bandwidth.common import pair_target, compact_target_text, pair_side_text, grouped
from sprinkler.base.results import CommandResultView
from sprinkler.utils.utils import positive_int_or_default

def log_traffic_plan(run_log, command_builder, link_mode, pairs):
    rows = plan_rows(command_builder, pairs)
    print_traffic_plan(run_log, rows, header="traffic_plan")


def log_summary_traffic_plan(run_log, phases):
    rows = []
    index_offset = 0
    for phase_label, command_builder, link_mode, pairs in phases:
        phase_rows = plan_rows(command_builder, pairs, index_offset=index_offset)
        index_offset += len(pairs)
        for row in phase_rows:
            row["group"] = f"{phase_label}:{row['group']}"
            rows.append(row)
    print_traffic_plan(run_log, rows, header="traffic_plan_summary", phase_total=len(phases))


def plan_rows(command_builder, pairs, index_offset=0):
    d2d_clients = {pair.get("client_target") for pair in pairs
                   if pair.get("kind") == "intra_d2d" and pair.get("client_target")}
    d2d_group_field = "client_target" if len(d2d_clients) == 1 else "server_target"
    return [row for index, pair in enumerate(pairs)
            for row in traffic_plan_rows(command_builder, pair, index_offset + index, d2d_group_field)]


def print_traffic_plan(run_log, rows, header, phase_total=1):
    if not rows:
        return
    groups = grouped(rows, lambda row: row["group"])
    show_link = has_link_distinction(rows)
    pair_total = len({row["pair"] for row in rows})
    run_log.info(f"{header} phase_total={phase_total} pair_total={pair_total} group_total={len(groups)}")
    for group_index, group in enumerate(groups, 1):
        group_total = len({row["pair"] for row in groups[group]})
        run_log.info(f"======= group={group_index:03d} machine={group} pair_total={group_total} =======")
        for role in ("server", "client"):
            role_rows = [row for row in groups[group] if row["role"] == role]
            if not role_rows:
                continue
            run_log.info(f"----- group={group_index:03d} role={role} count={len(role_rows)} -----")
            for row in role_rows:
                parts = [f"{role.upper()}:{row['target']}"]
                if show_link and display_link(row["link"]):
                    parts.append(f"link={row['link']}")
                if not row["ready"]:
                    parts.append(f"reason={row['reason']}")
                if row["cmd"]:
                    parts.append(f"cmd={row['cmd']}")
                message = " ".join(parts)
                run_log.log("INFO" if row["ready"] else "WARN", message)

def traffic_plan_rows(command_builder, pair, index, d2d_group_field):
    kind = str(pair.get("kind") or "") if isinstance(pair, dict) else ""
    group = pair.get(d2d_group_field) if kind == "intra_d2d" else kind if kind in {"intra_h2d", "intra_d2h"} else ""
    group = group or machine_pair(pair)
    link = pair.get("link") or ""
    reason, port = command_builder.not_ready_reason(pair, index)
    if reason:
        return [traffic_plan_row(pair, index, role, group, link, port, "", False, reason)
                for role in ("server", "client")]
    server = command_builder.listener(pair, port)
    client = command_builder.client(pair, port)
    return [traffic_plan_row(pair, index, role, group, link, port, item["cmd"], bool(item.get("ready")),
                             item.get("reason", ""))
            for role, item in (("server", server), ("client", client))]

def traffic_plan_row(pair, index, role, group, link, port, cmd, ready, reason):
    return {"pair": f"{int(index):03d}", "role": role, "group": group, "link": link,
            "target": pair_side_text(pair, role) or pair.get("target") or "",
            "port": port, "cmd": cmd, "ready": ready, "reason": reason or "ready"}

def stream_log_path(run_log):
    path = getattr(run_log, "path", None)
    return path / "streams" if path is not None else None

def create_stream_log(run_log, pair, index, role, side, command):
    stream_dir = stream_log_path(run_log)
    if stream_dir is None:
        return None
    name = run_log.formatter.safe_name(f"{int(index):03d}_{pair_target(pair)}_{role}")
    side_label = side.get("label") or side.get("host") or "local"
    return UrmaStreamLog(stream_dir / f"{name}.log", role, side_label, command)

class UrmaFlowSession:
    def __init__(self, runner, pair, index):
        self.runner = runner
        self.pair = pair
        self.index = index
        self.result_item = None
        self.server_process = self.client_process = None
        self.listener, self.client = {}, {}
        planner = runner.command_builder.port_planner
        self.port = planner.port(pair, index)
        if not pair.get("ok"):
            self.result_item = fail_pair(pair, pair.get("reason") or "bad pair", runner.link_mode)
        elif not planner.port_in_range(self.port):
            self.result_item = fail_pair(pair, f"port out of range: {self.port}", runner.link_mode)
        else:
            self.listener = runner.command_builder.listener(pair, self.port)
            self.client = runner.command_builder.client(pair, self.port)
            if not self.listener.get("ready") or not self.client.get("ready"):
                self.result_item = fail_pair(pair, "udma/eid_idx not resolved", runner.link_mode)

    def start(self, role):
        if self.result_item is not None:
            return
        item = self.listener if role == "server" else self.client
        side = self.runner.show_resolver.side(self.pair, role)
        command = item["cmd"]
        stream_log = create_stream_log(self.runner.run_log, self.pair, self.index, role, side, command)
        process = UrmaProcessSession(self.runner.executor, side, command, role, stream_log)
        setattr(self, f"{role}_process", process)
        result = process.start()
        if not result.get("ok"):
            self.runner.record_step(role, side, result, self.pair, self.index, command)
            if role == "client":
                self.stop()
            result_command = self.client["cmd"] if role == "server" else command
            self.result_item = traffic_pair_result(self.pair, result_command, False, result, role,
                                                   link_mode=self.runner.link_mode)

    def stop(self):
        for process in (self.client_process, self.server_process):
            if process is not None:
                process.stop()

    def collect(self):
        if self.result_item is not None:
            return self.result_item
        client = self.client_process.collect() if self.client_process else empty_process_result()
        server = self.server_process.collect() if self.server_process else empty_process_result()
        ok = bool(client.get("ok")) and bool(server.get("ok"))
        reason = "done" if ok else "client_or_server_failed"
        return traffic_pair_result(self.pair, self.client["cmd"], ok, client, reason, server,
                                   link_mode=self.runner.link_mode)

class UrmaTrafficRunner:
    def __init__(self, args=None, run_log=None, command_builder=None, show_resolver=None):
        self.args = args
        self.run_log = run_log
        self.executor = UrmaCommandExecutor(args=args)
        self.command_builder = command_builder or UrmaCommandBuilder(args)
        self.show_resolver = show_resolver or UrmaShowResolver(self.executor)
        self.link_mode = args_link_mode(args)
        self.start_stagger = DEFAULT_START_STAGGER_SECONDS
        self.listener_ready_wait = DEFAULT_LISTENER_READY_SECONDS

    def plan(self, pairs, log_plan=True):
        resolved = self.show_resolver.fill(pairs)
        if self.run_log is not None:
            failed = sum(1 for pair in resolved if not pair.get("ok", True))
            if failed:
                self.run_log.log("WARN", f"urma_show_failed={failed} pair_total={len(resolved)}")
            if resolved and log_plan:
                log_traffic_plan(self.run_log, self.command_builder, self.link_mode, resolved)
        return resolved

    def run(self, pairs):
        resolved = self.show_resolver.fill(pairs)
        if self.run_log is not None:
            failed = sum(1 for pair in resolved if not pair.get("ok", True))
            if failed:
                self.run_log.log("WARN", f"urma_show_failed={failed} pair_total={len(resolved)}")
            if resolved:
                log_traffic_plan(self.run_log, self.command_builder, self.link_mode, resolved)
        sessions, results = self.start_sessions(resolved)
        active = [session for session in sessions if results[session.index] is None]
        if active:
            duration = positive_int_or_default(getattr(self.args, "duration", 30), 30, "-duration")
            if self.run_log is not None:
                self.run_log.info(f"traffic_measure_start active={len(active)} duration={duration}s "
                                  f"sample_tail={DEFAULT_SAMPLE_TAIL}")
            try:
                time.sleep(duration)
            finally:
                self.run_concurrent(active, lambda session: session.stop())
                for session in active:
                    self.collect_session(session, results)
        results = [result for result in results if result is not None]
        if self.run_log is not None:
            for result in results:
                self.run_log.write_target_results(result.get("target") or "urma", [result])
        return results

    def execute(self, resolved, silent=False):
        if not resolved:
            return []
        sessions, results = self.start_sessions(resolved, silent=silent)
        active = [session for session in sessions if results[session.index] is None]
        if active:
            duration = positive_int_or_default(getattr(self.args, "duration", 30), 30, "-duration")
            if self.run_log is not None and not silent:
                self.run_log.info(f"traffic_measure_start active={len(active)} duration={duration}s "
                                  f"sample_tail={DEFAULT_SAMPLE_TAIL}")
            try:
                time.sleep(duration)
            finally:
                self.run_concurrent(active, lambda session: session.stop())
                for session in active:
                    self.collect_session(session, results)
        results = [result for result in results if result is not None]
        if self.run_log is not None:
            for result in results:
                self.run_log.write_target_results(result.get("target") or "urma", [result])
        return results

    def start_sessions(self, resolved, silent=False):
        sessions = [UrmaFlowSession(self, pair, index) for index, pair in enumerate(resolved)]
        results = [session.result_item for session in sessions]
        runnable = [session for session in sessions if session.result_item is None]
        if self.run_log is not None and not silent:
            self.run_log.info(f"traffic_start size={len(runnable)}")
            stream_dir = stream_log_path(self.run_log)
            if runnable and stream_dir is not None:
                self.run_log.info(f"stream_log_dir={stream_dir}")
        self.run_start_step(runnable, "server")
        client_runnable = [session for session in runnable if session.result_item is None]
        if client_runnable and self.listener_ready_wait > 0:
            time.sleep(self.listener_ready_wait)
        self.run_start_step(client_runnable, "client")
        for session in runnable:
            if session.result_item is not None:
                results[session.index] = session.result_item
        return sessions, results

    def run_start_step(self, sessions, role):
        def group_key(session):
            side = self.show_resolver.side(session.pair, role)
            if side.get("kind") == "local":
                return "local"
            return str(side.get("host") or "") or "local"

        def start_group(group):
            for index, session in enumerate(group):
                if index and self.start_stagger:
                    time.sleep(self.start_stagger)
                session.start(role)

        self.run_concurrent(list(grouped(sessions, group_key).values()), start_group)

    def run_concurrent(self, items, action):
        if not items:
            return
        with ThreadPoolExecutor(max_workers=len(items)) as pool:
            list(pool.map(action, items))

    def collect_session(self, session, results):
        try:
            results[session.index] = session.collect()
        except Exception as exc:
            results[session.index] = fail_pair(session.pair, f"collect failed: {exc}",
                                               link_mode=self.link_mode)
            if self.run_log is not None:
                self.run_log.log("WARN", f"collect failed pair={int(session.index):03d} "
                                         f"target={pair_target(session.pair)} reason={exc}")

    def record_step(self, step, side, result, pair=None, index="", command=""):
        if self.run_log is None:
            return
        pair_label = pair_target(pair) if pair else ""
        if pair and pair.get("kind") in {"intra_h2d", "intra_d2h"}:
            pair_label = f"{pair.get('kind')}:{compact_target_text(pair)}"
        display_cmd = command or result.get("cmd", "")
        parts = ([f"pair={int(index):03d}"] if index != "" else [])
        parts += [f"step={step}", f"group={machine_pair(pair)}",
                  f"side={side.get('label') or side.get('host') or 'local'}"]
        extras = (("target", pair_label), ("link", pair.get("link") if pair else ""),
                  ("link_mode", self.link_mode), ("cmd", display_cmd))
        parts.extend(f"{name}={value}" for name, value in extras if value)

        item = {**result, "traffic_step": step}
        if display_cmd:
            item.update(raw_cmd=item.get("cmd", ""), cmd=display_cmd)
        if pair_label:
            item["pair_target"] = pair_label
        ok = self.run_log.formatter.result_ok(item)
        level = "PASS" if ok else "FAIL"
        message = " ".join([*parts, f"rc={item.get('rc', '')}",
                            f"elapsed={self.run_log.formatter.elapsed_text(item.get('elapsed'))}s"])
        self.run_log.log(level, message)
