"""Run URMA bandwidth checks from planned EID pairs."""

from types import SimpleNamespace

from sprinkler.auto.bandwidth.report import BandwidthReport
from sprinkler.auto.bandwidth.common import mode_list, xboard_link_mode, XBOARD_MODES, UBOE_MODES, UBOE_XBOARD_MODE, INTRA_MODES, unique_ints, tag_results
from sprinkler.auto.bandwidth.planning.eid_pairs import pair_ranks as plan_pair_ranks, partition_executable_pairs, plan_eid_pairs
from sprinkler.auto.bandwidth.planning.ping_check import PingCheckRunner, PingPrecheck
from sprinkler.auto.bandwidth.urma.results import UrmaBandwidthResultView
from sprinkler.auto.bandwidth.urma.planner import UrmaTrafficRunner, log_summary_traffic_plan
from sprinkler.auto.health.config_hugepage import ensure_hugepages, hugepages_configured
from sprinkler.base.log import RunLog
from sprinkler.base.ranges import parse_range

CHECK_ID = "urma_bandwidth"

def parse_pair_spec(text):
    from sprinkler.auto.bandwidth.common import parse_pair_spec as common_parse
    return common_parse(text)

class UrmaBandwidthRequest:
    def __init__(self, args):
        self.args = args
        self.values = [str(value).strip() for value in (getattr(args, "urma_bandwidth", None) or [])
                       if str(value).strip()]
        self.mode_text = str(getattr(args, "mode", "") or "").lower()
        self.rank = ""
        self.master_rank = ""
        self.server_rank = str(getattr(args, "server_rank", "") or "").strip()
        self.modes = []

    def apply(self):
        self.parse_positionals()
        self.modes = mode_list(self.mode_text)
        xboard_link_mode(self.modes, getattr(self.args, "link_mode", "all"))
        arg_rank = str(getattr(self.args, "rank", "") or "").strip()
        # xd2d/ubg 不设默认 rank（跨板模式按拓扑自动发现），
        # 但必须保留用户显式传入的 -rank（如 -rank 0-7 要打7张卡而非全部）。
        # 由于 argparse 默认值也是 "0-7"，无法通过值本身区分"用户传了"和"默认值"，
        # 改用扫描 sys.argv 的方式判断。
        if self.modes[0] in {*XBOARD_MODES, UBOE_XBOARD_MODE} and not self.rank:
            from sprinkler.utils.utils import argv_has_flag
            if not argv_has_flag("-rank"):
                arg_rank = ""
        self.rank = self.rank or arg_rank or ("" if self.modes[0] in {*XBOARD_MODES, UBOE_XBOARD_MODE} else "0-7")
        if any(mode not in {*INTRA_MODES, *XBOARD_MODES, *UBOE_MODES} for mode in self.modes):
            raise ValueError(f"bad -urma-bandwidth mode: {self.mode_text}; "
                             f"expected h2d/d2h/d2d/xd2d/ubg/uboe/uboe-xboard/all or comma combinations")
        if self.server_rank and self.modes[0] not in {*XBOARD_MODES, UBOE_XBOARD_MODE}:
            raise ValueError("-server-rank is only supported by xd2d/ubg/uboe-xboard bandwidth mode")
        if self.server_rank:
            parse_range(self.server_rank)
        if self.master_rank and not str(self.master_rank).isdecimal():
            raise ValueError(f"bad d2d main rank: {self.master_rank}")
        self.set_arg("rank", self.rank, "-rank")
        if self.master_rank:
            self.set_arg("d2d_main", int(self.master_rank), "-d2d-main")
        setattr(self.args, "bandwidth_modes", self.modes)
        return self.modes

    def parse_positionals(self):
        if len(self.values) > 3:
            raise ValueError(f"too many positional args for -urma-bandwidth: {self.values}")
        if not self.values:
            return
        first = self.values[0].lower()
        first_parts = [part.strip() for part in first.split(",") if part.strip()]
        if first_parts and all(part in {"h2d", "d2h", "d2d", "xd2d", "ubg", "uboe", "uboe-xboard", "all"}
                               for part in first_parts):
            self.mode_text = first
            self.rank = self.values[1] if len(self.values) >= 2 else ""
            self.master_rank = self.values[2] if len(self.values) >= 3 else ""
        else:
            self.rank = self.values[0]
            self.master_rank = self.values[1] if len(self.values) >= 2 else ""

    def set_arg(self, name, value, label):
        current = getattr(self.args, name, None)
        if name == "rank" and current == "0-7":
            from sprinkler.utils.utils import argv_has_flag
            if not argv_has_flag("-rank"):
                current = ""
        if current not in ("", None) and current != value:
            raise ValueError(f"{label} conflicts with -urma-bandwidth positional value: {value}")
        setattr(self.args, name, value)

def run_urma_bandwidth(args, run_log):
    if not getattr(args, "bandwidth_modes", None):
        UrmaBandwidthRequest(args).apply()
    if getattr(args, "bandwidth_modes", [None])[0] in UBOE_MODES:
        # UBOE is a separate backend (device-side ethernet parameter plane); it bypasses
        # the intra h2d ping pre-check and planning entirely. Cross-board mirrors the
        # local backend but sources peer cards over -ip.
        from sprinkler.auto.bandwidth.uboe.planner import (
            run_uboe_bandwidth, run_uboe_xboard_bandwidth)
        if getattr(args, "bandwidth_modes", [None])[0] == UBOE_XBOARD_MODE:
            return run_uboe_xboard_bandwidth(args, run_log)
        return run_uboe_bandwidth(args, run_log)
    has_intra_pair = bool(getattr(args, "pair_spec", ""))
    has_xboard_pair = bool(getattr(args, "xboard_pair_spec", ""))
    if has_intra_pair and has_xboard_pair:
        return run_mixed_bandwidth(args, run_log)
    mode_values = getattr(args, "bandwidth_modes", [None])
    if mode_values[0] in XBOARD_MODES or (mode_values == ["d2d"] and has_xboard_pair):
        return run_local_xboard_bandwidth(args, run_log)
    ping_args = SimpleNamespace(**{**vars(args), "mode": "h2d"})
    _, ranks, pairs = plan_eid_pairs(ping_args, run_log)
    run_log.info(f"ping_phase=h2d rank={','.join(str(rank) for rank in ranks)} pair_total={len(pairs)}")
    passed_pairs, ping_failed = PingPrecheck(ping_args, run_log, PingCheckRunner).run(pairs)
    tag_results(ping_failed, execution_status="ping_failed", bandwidth_phase="h2d", ping=False)
    passed_ranks = {rank for pair in passed_pairs for rank in plan_pair_ranks(pair) if rank is not None}
    passed_targets = {pair.get("target") for pair in passed_pairs if pair.get("target")}
    if "d2d" in getattr(args, "bandwidth_modes", []) and getattr(args, "pair_spec", ""):
        for client_rank, server_rank in parse_pair_spec(getattr(args, "pair_spec", "")):
            passed_ranks.add(client_rank)
            passed_ranks.add(server_rank)
    results = list(ping_failed)
    for mode in getattr(args, "bandwidth_modes", None):
        results.extend(run_urma_bandwidth_mode(args, run_log, mode, passed_ranks, passed_targets))
    return results

def _prepare_intra_phase(args, run_log):
    intra_args = SimpleNamespace(**{**vars(args), "mode": "d2d"})
    _, _, intra_pairs = plan_eid_pairs(intra_args, run_log)
    intra_pairs = [p for p in intra_pairs if p.get("ok", True)]
    run_log.info(f"mixed_intra_phase=d2d pair_spec={getattr(args, 'pair_spec', '')} pair_total={len(intra_pairs)}")
    return intra_pairs, intra_args


def _prepare_xboard_phase(args, run_log, xboard_mode):
    values = dict(vars(args))
    values["mode"] = xboard_mode
    values["pair_spec"] = ""
    xboard_args = SimpleNamespace(**values)
    _, _, xboard_pairs = plan_eid_pairs(xboard_args, run_log)
    total_planned = len(xboard_pairs)
    xboard_pairs, skipped = partition_executable_pairs(xboard_pairs)
    xboard_pairs = [p for p in xboard_pairs if p.get("ok", True)]
    run_log.info(f"mixed_xboard_phase={xboard_mode} xboard_pair_spec={getattr(args, 'xboard_pair_spec', '')} "
                 f"pair_total={len(xboard_pairs)} planned={total_planned} skipped_no_topo_mapping={skipped}")
    traffic_pairs, ping_failed = PingPrecheck(xboard_args, run_log, PingCheckRunner).run(xboard_pairs)
    run_log.log("WARN" if ping_failed else "INFO",
                f"mixed_xboard_{xboard_mode} ping_check={'failed' if ping_failed else 'passed'} total={len(traffic_pairs)}")
    return traffic_pairs, xboard_args


def _plan_pairs(pairs, args, run_log):
    if not pairs:
        return []
    runner = UrmaTrafficRunner(args=args, run_log=run_log)
    return runner.plan(pairs)


def _execute_pairs(resolved, args, run_log):
    if not resolved:
        return []
    runner = UrmaTrafficRunner(args=args, run_log=run_log)
    traffic_results = runner.execute(resolved, silent=True)
    return traffic_results


def _xboard_mode_from(original_mode):
    if original_mode in XBOARD_MODES:
        return original_mode
    return "d2d" if original_mode == "d2d" else "xd2d"


def run_mixed_bandwidth(args, run_log):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = []
    original_mode = getattr(args, "bandwidth_modes", ["xd2d"])[0]
    has_intra = bool(getattr(args, "pair_spec", ""))
    has_xboard = bool(getattr(args, "xboard_pair_spec", ""))

    if has_intra and has_xboard:
        xboard_mode = _xboard_mode_from(original_mode)
        intra_pairs, intra_args = _prepare_intra_phase(args, run_log)
        xboard_pairs, xboard_args = _prepare_xboard_phase(args, run_log, xboard_mode)
        run_log.info("traffic_plan phase=2")
        phases = []
        intra_resolved = []
        if intra_pairs:
            intra_runner = UrmaTrafficRunner(args=intra_args, run_log=run_log)
            intra_resolved = intra_runner.plan(intra_pairs, log_plan=False)
            phases.append(("intra_d2d", intra_runner.command_builder, intra_runner.link_mode, intra_resolved))
        xboard_resolved = []
        if xboard_pairs:
            xboard_runner = UrmaTrafficRunner(args=xboard_args, run_log=run_log)
            xboard_resolved = xboard_runner.plan(xboard_pairs, log_plan=False)
            phases.append((xboard_mode, xboard_runner.command_builder, xboard_runner.link_mode, xboard_resolved))
        log_summary_traffic_plan(run_log, phases)
        run_log.info("traffic_start parallel phase=2")
        with ThreadPoolExecutor(max_workers=2) as executor:
            intra_future = executor.submit(_execute_pairs, intra_resolved, intra_args, run_log)
            xboard_future = executor.submit(_execute_pairs, xboard_resolved, xboard_args, run_log)
            for future in as_completed([intra_future, xboard_future]):
                traffic_results = future.result()
                phase = "mixed_intra_d2d" if future == intra_future else f"mixed_xboard_{xboard_mode}"
                results.extend(tag_results(traffic_results, bandwidth_phase=phase, ping=True))
    else:
        if has_intra:
            intra_pairs, intra_args = _prepare_intra_phase(args, run_log)
            if intra_pairs:
                intra_resolved = _plan_pairs(intra_pairs, intra_args, run_log)
                traffic_results = _execute_pairs(intra_resolved, intra_args, run_log)
                results.extend(tag_results(traffic_results, bandwidth_phase="mixed_intra_d2d", ping=True))
        if has_xboard:
            xboard_mode = _xboard_mode_from(original_mode)
            xboard_pairs, xboard_args = _prepare_xboard_phase(args, run_log, xboard_mode)
            if xboard_pairs:
                xboard_resolved = _plan_pairs(xboard_pairs, xboard_args, run_log)
                traffic_results = _execute_pairs(xboard_resolved, xboard_args, run_log)
                results.extend(tag_results(traffic_results, bandwidth_phase=f"mixed_xboard_{xboard_mode}", ping=True))
    return results

def run_local_xboard_bandwidth(args, run_log):
    mode = getattr(args, "bandwidth_modes", ["xd2d"])[0]
    phase_args = SimpleNamespace(**{**vars(args), "mode": mode})
    _, ranks, pairs = plan_eid_pairs(phase_args, run_log)
    tag_results(pairs, pair_source="local_xboard_inventory")
    pairs, skipped = partition_executable_pairs(pairs)
    run_log.info(f"phase={mode} rank={','.join(str(rank) for rank in ranks)} pair_total={len(pairs)} "
                 f"skipped_no_topo_mapping={skipped}")
    traffic_pairs, ping_failed = PingPrecheck(phase_args, run_log, PingCheckRunner).run(pairs)
    results = tag_results(ping_failed, execution_status="ping_failed", bandwidth_phase=mode, ping=False)
    traffic_results = UrmaTrafficRunner(args=phase_args, run_log=run_log).run(traffic_pairs) if traffic_pairs else []
    results += tag_results(traffic_results, bandwidth_phase=mode, ping=True)
    return results

def check_cpu_hugepages(run_log):
    if hugepages_configured():
        return
    run_log.info("检测到未配置大页，自动进行配置")
    ensure_hugepages(run_log)

def run_urma_bandwidth_mode(args, run_log, mode, passed_ranks, passed_targets):
    if mode in {"h2d", "d2h"}:
        check_cpu_hugepages(run_log)
    phase_args = SimpleNamespace(**{**vars(args), "mode": mode})
    _, ranks, pairs = plan_eid_pairs(phase_args, run_log)
    pairs, skipped = partition_executable_pairs(pairs)
    pairs = [pair for pair in pairs if pair_ping_passed(pair, passed_ranks, passed_targets)]
    run_log.info(f"phase={mode} rank={','.join(str(rank) for rank in ranks)} pair_total={len(pairs)} "
                 f"skipped_no_topo_mapping={skipped}")
    traffic_results = UrmaTrafficRunner(args=phase_args, run_log=run_log).run(pairs) if pairs else []
    tag_results(traffic_results, bandwidth_phase=mode, ping=True)
    return traffic_results

def pair_ping_passed(pair, passed_ranks, passed_targets):
    ranks = unique_ints((pair.get("client_rank"), pair.get("server_rank")))
    target = pair.get("target")
    return all(rank in passed_ranks for rank in ranks) if ranks else not target or target in passed_targets

def _extract_bw_opts_text(args):
    """Gather --bw_unit candidates from urma_opts / uboe_opts."""
    return " ".join(str(getattr(args, name, "") or "") for name in ("urma_opts", "uboe_opts"))


def urma_bandwidth_execute(args):
    from sprinkler.auto.bandwidth.urma.results import bw_display_unit

    run_log = RunLog(args, default_check_id=CHECK_ID)
    run_log.info(f"log_dir={run_log.path}")
    try:
        results = run_urma_bandwidth(args, run_log)
    except Exception as exc:
        return run_log.fail_error(CHECK_ID, exc)

    bw_unit = bw_display_unit(_extract_bw_opts_text(args))
    return run_log.finish_results(CHECK_ID, results, UrmaBandwidthResultView(run_log.formatter, bw_unit=bw_unit),
                                  BandwidthReport(results, unit=bw_unit))
