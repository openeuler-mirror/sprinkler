"""Run cluster bandwidth checks across multiple nodes."""

from types import SimpleNamespace

from sprinkler.base.log import RunLog
from sprinkler.base.ranges import parse_range
from sprinkler.auto.bandwidth.topology.inventory import ClusterPairBuilder, remote_inventory_parts
from sprinkler.auto.bandwidth.common import mode_list, xboard_link_mode, DEFAULT_MODE_TEXT, int_value, pair_result, xboard_pair_result, is_xboard_pair, has_link_distinction, has_target_selector, xboard_mapping_mode, selected_xboard_pairs, validate_xboard_pairs, tag_results
from sprinkler.auto.bandwidth.report import BandwidthReport
from sprinkler.auto.bandwidth.planning.eid_pairs import plan_eid_pairs
from sprinkler.auto.bandwidth.planning.ping_check import PingCheckRunner, PingPrecheck
from sprinkler.auto.bandwidth.urma.results import UrmaBandwidthResultView
from sprinkler.auto.bandwidth.urma.planner import UrmaTrafficRunner
from sprinkler.connect.targets import TargetContext

CHECK_ID = "cluster_bw"

def cluster_bw_validate(args):
    try:
        xboard_link_mode(cluster_mode(args), getattr(args, "link_mode", "all"))
        cluster_bw_parts(args)
        return ""
    except ValueError as exc:
        return str(exc)

def cluster_mode(args):
    text = str(getattr(args, "mode", "") or DEFAULT_MODE_TEXT).strip().lower()
    if text == DEFAULT_MODE_TEXT:
        return "xd2d"
    modes = mode_list(text)
    if modes not in (["xd2d"], ["ubg"]):
        raise ValueError("-cluster-bw -mode must be xd2d or ubg")
    return modes[0]

def cluster_bw_parts(args):
    items = [str(value).strip() for value in (getattr(args, "cluster_bw", None) or []) if str(value).strip()]
    named_spec = str(getattr(args, "server_rank", "") or "").strip()
    max_items = 1 if named_spec else 2
    if len(items) > max_items:
        raise ValueError(f"too many positional args for -cluster-bw: {items}")
    spec = named_spec or (items[0] if items else "")
    if not spec:
        raise ValueError("-cluster-bw requires server rank, for example: -cluster-bw 0-7 \"{s0:[c8:0]}\"")
    client_spec = items[0] if named_spec and items else (items[1] if len(items) >= 2 else "")
    return parse_range(spec), cluster_bw_client_node(args, client_spec)

def cluster_bw_client_node(args, spec):
    if not spec:
        from sprinkler.auto.bandwidth.common import has_target_selector
        if has_target_selector(args):
            return ""
        raise ValueError("-cluster-bw requires client targets via -node or -ip")
    if is_client_alias(spec):
        raise ValueError("client alias like A or A-G is not supported; use target selector like \"{s0:[c8:0]}\"")
    if str(getattr(args, "ip", "") or "").strip():
        raise ValueError("use either -cluster-bw <server-rank> <client-node-selector> or -ip, not both")
    if str(getattr(args, "node", "") or "").strip():
        raise ValueError("use either -cluster-bw <server-rank> <client-node-selector> or -node, not both")
    return spec

def cluster_bw_remote_args(args, node=None):
    if node is None:
        _ranks, node = cluster_bw_parts(args)
    values = {**vars(args), **({"node": node} if node else {})}
    return SimpleNamespace(**values)

def is_client_alias(spec):
    tokens = [token.strip() for token in str(spec or "").replace(" ", "").split(",") if token.strip()]
    parts = [token.split("-", 1) for token in tokens]
    return bool(parts) and all(len(item) <= 2 and all(len(part) == 1 and part.isalpha() for part in item)
                               for item in parts)

class ClusterBandwidthPlanner:
    def __init__(self, args):
        self.ranks, node = cluster_bw_parts(args)
        self.args = cluster_bw_remote_args(args, node)
        self.client_ranks = parse_range(str(getattr(self.args, "rank", "") or "0-7"))
        self.client_rank_set = set(self.client_ranks)
        self.same_plane = bool(getattr(self.args, "same_plane", False))
        self.all2all = bool(getattr(self.args, "all2all", False))
        self.mode = cluster_mode(self.args)
        self.args.mode = self.mode
        self.mapping_mode = xboard_mapping_mode(same_plane=self.same_plane, all2all=self.all2all)
        self.selection = {"ranks": set(self.ranks),
                          "link_mode": xboard_link_mode(self.mode, getattr(self.args, "link_mode", "all")),
                          "same_plane": self.same_plane, "all2all": self.all2all}
        target_context = TargetContext(self.args)
        self.remote_plan = target_context.remote_plan

class ClusterBandwidthAction:
    def __init__(self, args, run_log):
        self.args = args
        self.run_log = run_log

    def run(self):
        planner = ClusterBandwidthPlanner(self.args)
        pairs = self.executable_pairs(planner)
        if not pairs:
            pairs = self.selected_target_pairs(planner)
        if not pairs:
            raise ValueError("-cluster-bw requires xboard eid_pairs or inventory from selected client targets")
        return self.run_executable_pairs(planner, pairs)

    def executable_pairs(self, planner):
        remote_plan = planner.remote_plan
        if not isinstance(remote_plan, dict):
            return []
        pairs = remote_plan.get("eid_pairs") or []
        if not pairs:
            return self.inventory_pairs(planner, remote_plan)
        xboard_pairs = [dict(pair) for pair in pairs if is_xboard_pair(pair)]
        if not xboard_pairs:
            raise ValueError("remote_plan has no xboard eid_pairs for -cluster-bw")
        validate_xboard_pairs(xboard_pairs, require_client_machine=True, **planner.selection)
        selected = self.client_rank_pairs(planner, selected_xboard_pairs(xboard_pairs, **planner.selection))
        if not selected:
            raise ValueError("remote_plan has no xboard eid_pairs matching -cluster-bw")
        return tag_results(selected, pair_source="remote_plan_eid_pairs")

    def inventory_pairs(self, planner, remote_plan):
        local_inventory, target_inventories, errors = remote_inventory_parts(remote_plan)
        if not local_inventory and not target_inventories:
            return []
        if not local_inventory or not target_inventories:
            if local_inventory and errors:
                return tag_results(errors, pair_source="remote_plan_inventory")
            raise ValueError("remote_plan cluster inventory requires local_inventory and target_inventories")
        builder = ClusterPairBuilder(local_inventory, target_inventories, edge_strict=False)
        pairs = builder.xboard_pairs(sorted(planner.ranks), link_mode=planner.selection["link_mode"],
                                     same_plane=planner.same_plane, all2all=planner.all2all)
        selected = self.client_rank_pairs(planner, selected_xboard_pairs(pairs, **planner.selection)) if pairs else []
        if selected:
            return tag_results([*selected, *errors], pair_source="remote_plan_inventory")
        if errors:
            return tag_results(errors, pair_source="remote_plan_inventory")
        if pairs:
            raise ValueError("remote_plan cluster inventory generated no xboard pairs matching -cluster-bw")
        raise ValueError("remote_plan cluster inventory generated no xboard pairs")

    def selected_target_pairs(self, planner):
        if planner.remote_plan or not has_target_selector(planner.args):
            return []
        values = {**vars(planner.args), "mode": planner.mode,
                  "rank": ",".join(str(rank) for rank in planner.ranks)}
        phase_args = SimpleNamespace(**values)
        _, _, pairs = plan_eid_pairs(phase_args, self.run_log)
        return tag_results(self.client_rank_pairs(planner, pairs), pair_source="selected_target_inventory")

    def client_rank_pairs(self, planner, pairs):
        return [pair for pair in pairs if self.client_rank_selected(planner, pair)]

    def client_rank_selected(self, planner, pair):
        rank = int_value(pair.get("client_rank"))
        return not is_xboard_pair(pair) or rank is None or rank in planner.client_rank_set

    def run_executable_pairs(self, planner, pairs):
        executable_pairs = []
        pre_results = []
        for pair in pairs:
            if is_xboard_pair(pair) and bool(pair.get("ok", True)):
                executable_pairs.append(pair)
            else:
                pre_results.append(pair)

        source_values = dict.fromkeys(str(pair.get("pair_source") or "").strip() for pair in pairs)
        sources = ",".join(source for source in source_values if source) or "unknown"
        self.run_log.info(f"execution_status=running real_traffic=started pair_total={len(executable_pairs)} "
                          f"skipped_total={len(pre_results)} pair_source={sources} "
                          f"server_rank={','.join(str(rank) for rank in planner.ranks)} "
                          f"client_rank={','.join(str(rank) for rank in planner.client_ranks)} "
                          f"mapping_mode={planner.mapping_mode} "
                          f"link_mode={planner.selection['link_mode']}")
        results = tag_results(pre_results, execution_status="not_executable", real_traffic=False,
                              bandwidth_phase=CHECK_ID, ping=False)
        if executable_pairs:
            traffic_pairs, ping_failed = PingPrecheck(planner.args, self.run_log, PingCheckRunner).run(executable_pairs)
            results += tag_results(ping_failed, execution_status="ping_failed", real_traffic=False,
                                   bandwidth_phase=CHECK_ID, ping=False)
            if traffic_pairs:
                traffic = UrmaTrafficRunner(args=planner.args, run_log=self.run_log).run(traffic_pairs)
                results += tag_results(traffic, execution_status="executed", real_traffic=True,
                                       bandwidth_phase=CHECK_ID, ping=True)
        return self.run_log.finish_results(CHECK_ID, results, UrmaBandwidthResultView(self.run_log.formatter),
                                           BandwidthReport(results))

def cluster_bw_execute(args):
    run_log = RunLog(args, default_check_id=CHECK_ID)
    run_log.info(f"log_dir={run_log.path}")
    try:
        return ClusterBandwidthAction(args, run_log).run()
    except Exception as exc:
        return run_log.fail_error(CHECK_ID, exc)
