"""EID pair planning for URMA bandwidth."""

import copy
import json
from pathlib import Path

from sprinkler.auto.bandwidth.topology.inventory import ClusterInventoryBuilder, ClusterPairBuilder, ClusterInventoryCollector, remote_inventory_parts, local_xboard_pairs, local_xboard_pairs_from_inventory
from sprinkler.auto.bandwidth.common import unique_ints, int_value, pair_result, xboard_pair_result, is_xboard_pair, display_reason, display_link, pair_flow_text, has_link_distinction, has_target_selector, args_mode_list, args_link_mode, xboard_mapping_mode, selected_xboard_pairs, parse_pair_spec, DEFAULT_ROOTINFO, _is_ipv4
from sprinkler.auto.bandwidth.topology.rootinfo import RootInfoTopology
from sprinkler.auto.bandwidth.common.route_conf import RouteConf
from sprinkler.base.log import RunLog
from sprinkler.base.ranges import parse_range
from sprinkler.connect.base import ResultProcessor
from sprinkler.connect.ssh import SSHOSConnector
from sprinkler.connect.targets import TargetContext
from sprinkler.config.host_device_pairs import get_host_and_device_pairs
from sprinkler.utils.utils import env_json_b64

XBOARD_D2D_SOURCE_ERROR = "xd2d/ubg requires local rootinfo or CPU OS targets via -sp/-ca/-node/-ip"
MODE_KIND = {"h2d": "intra_h2d", "d2h": "intra_d2h", "d2d": "intra_d2d"}
PAIR_RANK_KEYS = {"intra_h2d": ("server_rank",), "intra_d2h": ("client_rank",),
                  "intra_d2d": ("client_rank", "server_rank"), "xboard_d2d": ("server_rank",)}

def no_topo_mapping(pair):
    return (str(pair.get("link") or "").lower() == "no_topo_mapping"
            or str(pair.get("reason") or "").lower() == "no_topo_mapping")

def partition_executable_pairs(pairs):
    items = list(pairs)
    runnable = [pair for pair in items if not no_topo_mapping(pair)]
    return runnable, len(items) - len(runnable)

def deduplicate_d2d_pairs(pairs):
    """去重: 每条无向 D2D 边只保留一份, 避免 A->B / B->A 重复打印.
    intra_d2d 无 link-mode(链路恒为 topo), 按 (rank min, max) 无向去重;
    xboard_d2d 才有 link-mode(L0/L1 等平面链路), 按 link + 无向端点对
    (machine, rank) 去重, 使不同平面链路不被合并, A:0-B:1 与 A:1-B:0
    两条不同链路不被误合并, 而 A:0-B:1 与 B:1-A:0 反向合并."""
    seen = set()
    result = []
    for pair in pairs:
        kind = str(pair.get("kind") or "")
        client = int_value(pair.get("client_rank"))
        server = int_value(pair.get("server_rank"))
        if kind == "intra_d2d" and client is not None and server is not None:
            key = (kind, min(client, server), max(client, server))
        elif kind == "xboard_d2d" and client is not None and server is not None:
            left = (str(pair.get("client_machine") or ""), client)
            right = (str(pair.get("server_machine") or ""), server)
            key = (kind, display_link(pair.get("link")), tuple(sorted((left, right))))
        else:
            key = None
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        result.append(pair)
    return result

def eid32(values, index):
    if len(values) <= index:
        return ""
    return RouteConf.format_eid32(values[index])

def host_device_eid_pairs(mode, ranks, host_device_pairs):
    pairs = []
    selected = set(int(rank) for rank in ranks)
    for rank, values in sorted(host_device_pairs.items()):
        rank = int(rank)
        if selected and rank not in selected:
            continue

        host_eid = eid32(values, 0)
        npu_eid = eid32(values, 1)
        host = {"rank": rank, "target": "CPU", "eid32": host_eid}
        npu = {"rank": rank, "target": f"NPU{rank}", "eid32": npu_eid, "execute_eid": npu_eid}
        # 当EID不可用且值是合法IPv4(静态配置场景)时, 携带原始IPv4供h2d ping用
        raw_npu = str(values[1]).strip() if len(values) > 1 else ""
        if not npu_eid and _is_ipv4(raw_npu):
            npu["ipv4"] = raw_npu
        client, server, runner = (npu, host, "npu") if mode == "d2h" else (host, npu, "host")
        # EID缺失但携带静态IPv4时, 该pair仍可ping(走IPv4),
        ok = bool(host_eid and npu_eid) or bool(npu.get("ipv4"))
        reason = "ready" if ok else "eid missing"
        pairs.append(pair_result(f"intra_{mode}", "intra", runner, client=client, server=server,
                                 link="topo", ok=ok, reason=reason))
    return pairs

def npu_side(endpoint, host_pairs=None):
    values = (host_pairs or {}).get(endpoint.rank, [])
    execute_eid = eid32(values, 1)
    # EID不可用但静态配置是合法IPv4时, 用IPv4作为执行地址(供ssh免密)
    if not execute_eid and len(values) > 1 and _is_ipv4(str(values[1])):
        execute_eid = str(values[1]).strip()
    return {"rank": endpoint.rank, "target": f"NPU{endpoint.rank}", "eid32": endpoint.eid32,
            "execute_eid": execute_eid}

def d2d_eid_pairs(topology, ranks, mode="one-to-many", master_rank=None, host_pairs=None):
    pairs = []
    for source, destination, link in topology.intra_d2d_pairs(ranks, mode=mode, master_rank=master_rank):
        no_topo = str(link or "") == "no_topo_mapping"
        ok = bool(source.eid32 and destination.eid32) and not no_topo
        if ok:
            reason = "ready"
        elif no_topo:
            reason = "no_topo_mapping"
        else:
            reason = "eid missing"
        pairs.append(pair_result("intra_d2d", "intra", "npu", client=npu_side(source, host_pairs),
                                 server=npu_side(destination, host_pairs), link=link or "", ok=ok, reason=reason,
                                 execution_status="skipped" if no_topo else "planned"))
    return pairs

def custom_d2d_eid_pairs(topology, custom_pairs, host_pairs=None):
    inventory = ClusterInventoryBuilder().from_topology(None, topology, host_pairs)
    builder = ClusterPairBuilder(inventory, [inventory])
    topology_ranks = set(topology.entries.keys())
    pairs = []
    for client_rank, server_rank in custom_pairs:
        if client_rank not in topology_ranks or server_rank not in topology_ranks:
            missing = [r for r in [client_rank, server_rank] if r not in topology_ranks]
            pairs.append(pair_result("intra_d2d", "intra", "npu",
                                     client={"rank": client_rank, "target": f"NPU{client_rank}", "eid32": "", "execute_eid": ""},
                                     server={"rank": server_rank, "target": f"NPU{server_rank}", "eid32": "", "execute_eid": ""},
                                     link="", ok=False, reason=f"rank {missing} not in topology (available: {sorted(topology_ranks)})", execution_status="skipped"))
            continue
        if topology.direct_pair(client_rank, server_rank):
            source = topology.route_endpoint("NPU", client_rank, topology.adjacency.get((client_rank, server_rank), ""))
            destination = topology.route_endpoint("NPU", server_rank, topology.adjacency.get((server_rank, client_rank), ""))
            link = "topo"
            no_topo = False
        else:
            client_rank_info = builder.rank_info(inventory, client_rank)
            server_rank_info = builder.rank_info(inventory, server_rank)
            links = builder.selected_links(client_rank_info, server_rank_info, "all")
            if not links:
                pairs.append(pair_result("intra_d2d", "intra", "npu",
                                         client={"rank": client_rank, "target": f"NPU{client_rank}", "eid32": "", "execute_eid": ""},
                                         server={"rank": server_rank, "target": f"NPU{server_rank}", "eid32": "", "execute_eid": ""},
                                         link="", ok=False, reason="no_topo_mapping", execution_status="skipped"))
                continue
            link = links[0]
            pair_data = builder.pair("local", 0, client_rank, server_rank, link, inventory,
                                     client_rank_info, server_rank_info, "one-to-many")
            if pair_data:
                pairs.append(pair_data[0])
            continue
        ok = bool(source.eid32 and destination.eid32)
        no_topo = not ok
        reason = "ready" if ok else "no_topo_mapping"
        rank_items = {int(item["rank"]): item for item in inventory.get("ranks") or []}
        client_item = rank_items.get(client_rank, {})
        server_item = rank_items.get(server_rank, {})
        client = {"rank": client_rank, "target": f"NPU{client_rank}", "eid32": source.eid32,
                  "execute_eid": client_item.get("execute_eid", source.eid32)}
        server = {"rank": server_rank, "target": f"NPU{server_rank}", "eid32": destination.eid32,
                  "execute_eid": server_item.get("execute_eid", destination.eid32)}
        pairs.append(pair_result("intra_d2d", "intra", "npu", client=client, server=server,
                                 link=link, ok=ok, reason=reason, execution_status="skipped" if no_topo else "planned"))
    return pairs

def custom_xboard_eid_pairs(topology, custom_pairs, link_mode="all", args=None, edge_strict=False):
    host_pairs = get_host_and_device_pairs()
    inventory = ClusterInventoryBuilder().from_topology(None, topology, host_pairs)
    builder = ClusterPairBuilder(inventory, [inventory], edge_strict=edge_strict)
    pairs = []
    for client_rank, server_rank in custom_pairs:
        client_rank_info = builder.rank_info(inventory, client_rank)
        server_rank_info = builder.rank_info(inventory, server_rank)
        if not client_rank_info or not server_rank_info:
            pairs.append(xboard_pair_result(
                client={"rank": client_rank, "target": f"NPU{client_rank}", "eid32": "", "execute_eid": ""},
                server={"rank": server_rank, "target": f"NPU{server_rank}", "eid32": "", "execute_eid": ""},
                runner="npu", link="", ok=False, reason="rank not in inventory", execution_status="skipped"))
            continue
        links = builder.selected_links(client_rank_info, server_rank_info, link_mode)
        if not links:
            pairs.append(xboard_pair_result(
                client={"rank": client_rank, "target": f"NPU{client_rank}", "eid32": "", "execute_eid": ""},
                server={"rank": server_rank, "target": f"NPU{server_rank}", "eid32": "", "execute_eid": ""},
                runner="npu", link="", ok=False, reason="no_topo_mapping", execution_status="skipped"))
            continue
        for link in links:
            pair_data = builder.pair("local", 0, client_rank, server_rank, link, inventory,
                                     client_rank_info, server_rank_info, "one-to-many")
            if pair_data:
                pairs.append(pair_data)
    return pairs

def custom_xboard_eid_pairs_with_targets(topology, custom_pairs, link_mode="all", args=None):
    if not args or not has_target_selector(args):
        return custom_xboard_eid_pairs(topology, custom_pairs, link_mode, args)
    context = TargetContext(args)
    manager = SSHOSConnector(args=args, result_processor=ResultProcessor())
    collector = ClusterInventoryCollector(args, manager)
    plans = []
    if str(getattr(args, "local", "") or "").strip():
        servers, clients = context.get_local_targets(), context.get_targets("cpu_os")
        for server in servers:
            key = (server.host, server.port)
            targets = [target for target in clients if (target.host, target.port) != key]
            client = manager.connect_client(server)
            try:
                plans.append(collector.collect(client, server, targets))
            finally:
                client.close()
    else:
        plans.append(collector.collect_command_host(context.get_targets("cpu_os")))
    pairs = []
    for plan in plans:
        local_inventory, target_inventories, errors = remote_inventory_parts(plan)
        if not local_inventory or not target_inventories:
            pairs.extend(errors)
            continue
        builder = ClusterPairBuilder(local_inventory, target_inventories, edge_strict=False)
        for client_rank, server_rank in custom_pairs:
            client_rank_info = builder.rank_info(local_inventory, client_rank)
            server_rank_info = builder.rank_info(local_inventory, server_rank)
            if not client_rank_info or not server_rank_info:
                continue
            links = builder.selected_links(client_rank_info, server_rank_info, link_mode)
            for link in links:
                pair_data = builder.pair("local", 0, client_rank, server_rank, link, local_inventory,
                                         client_rank_info, server_rank_info, "one-to-many")
                if pair_data:
                    pairs.append(pair_data)
        pairs.extend(errors)
    return pairs

def pair_ranks(pair):
    kind = str(pair.get("kind") or "").lower()
    keys = PAIR_RANK_KEYS.get(kind, ())
    return [int_value(pair.get(key)) for key in keys]

def pair_mode(pair, mode):
    from sprinkler.auto.bandwidth.common import XBOARD_MODES
    if is_xboard_pair(pair):
        return mode in XBOARD_MODES
    return str(pair.get("kind") or "").lower() == MODE_KIND.get(mode)

def ranks_from_pairs(pairs):
    return unique_ints(rank for pair in pairs for rank in pair_ranks(pair))

def preview_header(pair, show_link=False):
    if pair.get("kind") == "xboard_inventory_error":
        return f"kind={pair.get('kind') or ''} scope={pair.get('scope') or ''}"
    parts = [f"kind={pair.get('kind') or ''}", f"runner={pair.get('runner') or ''}"]
    link = display_link(pair.get("link"))
    if show_link and link:
        parts.append(f"link={link}")
    return " ".join(parts)

def preview_line(pair):
    if pair.get("kind") == "xboard_inventory_error":
        reason = display_reason(pair.get("reason")) or "inventory failed"
        return f"{pair.get('client_target') or 'client'} reason={reason}"
    line = pair_flow_text(pair, include_eid=True)
    reason = display_reason(pair.get("reason"))
    return f"{line} reason={reason}" if reason else line

class EidPairPlanContext:
    def __init__(self, args, run_log=None):
        self.args = args
        self.run_log = run_log
        self.mode_values = args_mode_list(args)
        self.mode_label = ",".join(self.mode_values)
        self.rank_spec = str(getattr(args, "rank", "") or "").strip()
        self.link_mode = args_link_mode(args)
        self.same_plane = bool(getattr(args, "same_plane", False))
        self.all2all = bool(getattr(args, "all2all", False))
        self.server_rank_spec = str(getattr(args, "server_rank", "") or "").strip()

    def plan(self):
        remote_plan = self.load_remote_plan()
        d2d_xboard = (self.mode_values == ["d2d"]
                      and str(getattr(self.args, "xboard_pair_spec", "") or "").strip()
                      and not str(getattr(self.args, "pair_spec", "") or "").strip())
        if self.mode_values[0] in {"xd2d", "ubg"} or d2d_xboard:
            return self.xboard_plan(remote_plan)

        if remote_plan.get("eid_pairs"):
            pairs = self.remote_pairs(remote_plan)
            ranks = self.ranks(remote_plan=remote_plan, pairs=pairs)
            return None, ranks, pairs

        host_pairs = get_host_and_device_pairs() if {"h2d", "d2h", "d2d"} & set(self.mode_values) else {}
        topology = RootInfoTopology.load(Path(DEFAULT_ROOTINFO).expanduser()) if "d2d" in self.mode_values else None
        ranks = self.ranks(topology=topology, host_pairs=host_pairs)
        pairs = []
        for mode in self.mode_values:
            if mode in {"h2d", "d2h"}:
                pairs += host_device_eid_pairs(mode, ranks, host_pairs)
            elif mode == "d2d":
                pair_spec = str(getattr(self.args, "pair_spec", "") or "").strip()
                if pair_spec:
                    custom_pairs = parse_pair_spec(pair_spec)
                    pairs += custom_d2d_eid_pairs(topology, custom_pairs, host_pairs)
                else:
                    inventory = ClusterInventoryBuilder().from_topology(None, topology, host_pairs)
                    pairs += local_xboard_pairs_from_inventory(topology, inventory, ranks, link_mode="all",
                                                               d2d_mode=getattr(self.args, "d2d_mode", "one-to-many"),
                                                               master_rank=getattr(self.args, "d2d_main", None))
            else:
                raise ValueError(XBOARD_D2D_SOURCE_ERROR)
        return None, ranks, pairs

    def xboard_plan(self, remote_plan):
        from sprinkler.auto.bandwidth.common import XBOARD_MODES
        xboard_pair_spec = str(getattr(self.args, "xboard_pair_spec", "") or "").strip()
        if xboard_pair_spec:
            custom_pairs = parse_pair_spec(xboard_pair_spec)
            edge_strict = self.mode_values == ["d2d"]
            if has_target_selector(self.args):
                pairs = self._xboard_pairs_from_remote_inventory(custom_pairs)
            else:
                topology = RootInfoTopology.load(Path(DEFAULT_ROOTINFO).expanduser())
                pairs = custom_xboard_eid_pairs(topology, custom_pairs, self.link_mode,
                                                edge_strict=edge_strict)
            from sprinkler.auto.bandwidth.common import validate_xboard_pairs
            validate_xboard_pairs(pairs)
            return None, ranks_from_pairs(pairs), pairs

        if not remote_plan:
            if not has_target_selector(self.args) and not str(getattr(self.args, "local", "") or "").strip():
                pair_spec = str(getattr(self.args, "pair_spec", "") or "").strip()
                topology = RootInfoTopology.load(Path(DEFAULT_ROOTINFO).expanduser())
                if pair_spec:
                    custom_pairs = parse_pair_spec(pair_spec)
                    pairs = custom_xboard_eid_pairs(topology, custom_pairs, self.link_mode,
                                                    edge_strict=(self.mode_values == ["d2d"]))
                    from sprinkler.auto.bandwidth.common import validate_xboard_pairs
                    validate_xboard_pairs(pairs)
                    return None, ranks_from_pairs(pairs), pairs
                ranks = parse_range(self.rank_spec) if self.rank_spec else []
                server_ranks = parse_range(self.server_rank_spec) if self.server_rank_spec else None
                pairs = local_xboard_pairs(ranks, self.link_mode, self.same_plane, self.all2all,
                                           getattr(self.args, "d2d_mode", "one-to-many"),
                                           getattr(self.args, "d2d_main", None), server_ranks,
                                           force_xboard=True)
                from sprinkler.auto.bandwidth.common import validate_xboard_pairs
                validate_xboard_pairs(pairs)
                return None, ranks or ranks_from_pairs(pairs), pairs
            return self.selected_target_plan()

        pairs = [dict(pair) for pair in remote_plan.get("eid_pairs") or [] if is_xboard_pair(pair)]
        if pairs:
            ranks = self.ranks(remote_plan=remote_plan, pairs=pairs)
            pairs = selected_xboard_pairs(pairs, ranks=ranks, link_mode=self.link_mode,
                                          same_plane=self.same_plane, all2all=self.all2all)
        else:
            pairs = self.inventory_pairs(remote_plan)
            ranks = self.ranks(remote_plan=remote_plan, pairs=pairs)
        from sprinkler.auto.bandwidth.common import validate_xboard_pairs
        validate_xboard_pairs(pairs)
        return None, ranks, pairs

    def _xboard_pairs_from_remote_inventory(self, custom_pairs):
        context = TargetContext(self.args)
        manager = SSHOSConnector(args=self.args, result_processor=ResultProcessor())
        collector = ClusterInventoryCollector(self.args, manager)
        plans = []
        if str(getattr(self.args, "local", "") or "").strip():
            servers, clients = context.get_local_targets(), context.get_targets("cpu_os")
            for server in servers:
                key = (server.host, server.port)
                targets = [target for target in clients if (target.host, target.port) != key]
                client = manager.connect_client(server)
                try:
                    plans.append(collector.collect(client, server, targets))
                finally:
                    client.close()
        else:
            plans.append(collector.collect_command_host(context.get_targets("cpu_os")))
        pairs = []
        d2d_style = self.mode_values == ["d2d"]
        for plan in plans:
            local_inventory, target_inventories, errors = remote_inventory_parts(plan)
            if not local_inventory or not target_inventories:
                pairs.extend(errors)
                continue
            builder = ClusterPairBuilder(local_inventory, target_inventories, edge_strict=d2d_style)
            for client_rank, server_rank in custom_pairs:
                server_rank_info = builder.rank_info(local_inventory, server_rank)
                if not server_rank_info:
                    if self.run_log:
                        self.run_log.info(f"xboard_pairs: server_rank={server_rank} not found in local inventory")
                    continue
                client_rank_info = None
                client_machine = "local"
                client_inventory = None
                for idx, target_inv in enumerate(target_inventories):
                    ci = builder.rank_info(target_inv, client_rank)
                    if ci:
                        client_rank_info = ci
                        client_machine = str(target_inv.get("client_machine") or target_inv.get("machine") or f"remote{idx}")
                        client_inventory = target_inv
                        break
                if not client_rank_info:
                    if self.run_log:
                        self.run_log.info(f"xboard_pairs: client_rank={client_rank} not found in target inventories")
                    continue
                if not client_rank_info or not server_rank_info:
                    continue
                links = builder.selected_links(client_rank_info, server_rank_info, self.link_mode)
                for link in links:
                    pair_data = builder.pair(client_machine, 0, client_rank, server_rank, link,
                                             client_inventory, client_rank_info, server_rank_info,
                                             self._mapping_mode())
                    if pair_data:
                        pairs.append(pair_data)
            pairs.extend(errors)
        return pairs

    def _mapping_mode(self):
        from sprinkler.auto.bandwidth.topology.inventory import xboard_mapping_mode
        return xboard_mapping_mode(same_plane=self.same_plane, all2all=self.all2all)

    def selected_target_plan(self):
        if not has_target_selector(self.args):
            raise ValueError(XBOARD_D2D_SOURCE_ERROR)

        context = TargetContext(self.args)
        manager = SSHOSConnector(args=self.args, result_processor=ResultProcessor())
        collector = ClusterInventoryCollector(self.args, manager)
        plans = []

        if str(getattr(self.args, "local", "") or "").strip():
            servers, clients = context.get_local_targets(), context.get_targets("cpu_os")
            for server in servers:
                key = (server.host, server.port)
                targets = [target for target in clients if (target.host, target.port) != key]
                client = manager.connect_client(server)
                try:
                    plans.append(collector.collect(client, server, targets))
                finally:
                    client.close()
        else:
            plans.append(collector.collect_command_host(context.get_targets("cpu_os")))

        pairs = [pair for plan in plans for pair in self.inventory_pairs(plan)]
        return None, ranks_from_pairs(pairs), pairs

    def inventory_pairs(self, remote_plan):
        local_inventory, target_inventories, errors = remote_inventory_parts(remote_plan)
        if not local_inventory or not target_inventories:
            if local_inventory and errors:
                return errors
            raise ValueError(XBOARD_D2D_SOURCE_ERROR)

        ranks = self.ranks(remote_plan=remote_plan)
        ranks = ranks or unique_ints(item.get("rank") for item in local_inventory.get("ranks") or [])
        builder = ClusterPairBuilder(local_inventory, target_inventories, edge_strict=False)
        pairs = builder.xboard_pairs(ranks, self.link_mode, self.same_plane, self.all2all)
        return [*pairs, *errors]

    def remote_pairs(self, remote_plan):
        raw_pairs = [dict(pair) for pair in remote_plan.get("eid_pairs") or [] if isinstance(pair, dict)]
        ranks = self.ranks(remote_plan=remote_plan, pairs=raw_pairs)
        rank_set = set(ranks)
        pairs = []
        for mode in self.mode_values:
            for pair in raw_pairs:
                if not pair_mode(pair, mode):
                    continue
                if ranks and not (set(pair_ranks(pair)) & rank_set):
                    continue
                if pair.get("kind") == "intra_d2h" and not pair.get("client_execute_eid"):
                    pair["client_execute_eid"] = pair.get("client_eid32", "")
                pairs.append(pair)

        if not pairs:
            raise ValueError(f"remote_plan has no {self.mode_label} eid_pairs matching request")
        return pairs

    def ranks(self, remote_plan=None, pairs=None, topology=None, host_pairs=None):
        if self.rank_spec:
            return parse_range(self.rank_spec)
        if remote_plan and remote_plan.get("ranks"):
            return unique_ints(remote_plan["ranks"])
        if pairs:
            return ranks_from_pairs(pairs)
        if topology is not None and topology.ranks():
            return topology.ranks()
        return sorted(int(rank) for rank in host_pairs or {})

    def load_remote_plan(self):
        path = str(getattr(self.args, "remote_plan", "") or "").strip()
        data = env_json_b64("SPRINKLER_REMOTE_PLAN_B64") or {}
        if not data and path:
            data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        return data

def plan_eid_pairs(args, run_log=None):
    return EidPairPlanContext(args, run_log).plan()

def get_eid_pairs(args):
    run_log = RunLog(args, default_check_id="eid_pairs")
    run_log.info(f"log_dir={run_log.path}")
    try:
        plan_args = copy.copy(args)
        plan_args.d2d_mode = "alltoall"
        _, ranks, pairs = plan_eid_pairs(plan_args, run_log)
        pairs = deduplicate_d2d_pairs(pairs)
        pairs = sorted(pairs, key=lambda item: (str(item.get("kind") or ""),
                                                str(display_link(item.get("link"))),
                                                int_value(item.get("client_rank"), 0),
                                                int_value(item.get("server_rank"), 0)))
        run_log.write_results(pairs)
        run_log.info(f"rank={','.join(str(rank) for rank in ranks)} pair_total={len(pairs)}")
        show_link = has_link_distinction(pairs)
        previous_header = ""
        for pair in pairs:
            level = "INFO" if run_log.formatter.result_ok(pair) or no_topo_mapping(pair) else "WARN"
            header = preview_header(pair, show_link)
            if header != previous_header:
                run_log.log("INFO", header)
                previous_header = header
            run_log.log(level, preview_line(pair))
        runnable_pairs, skipped = partition_executable_pairs(pairs)
        failed = sum(1 for pair in runnable_pairs if not run_log.formatter.result_ok(pair))
        runnable = len(runnable_pairs)
        run_log.info(f"skipped_no_topo_mapping={skipped}")
        run_log.auto_details("eid_pairs", runnable - failed, runnable)
        run_log.finish_log_dir()
        return 0 if failed == 0 else 1
    except Exception as exc:
        return run_log.fail_error("eid_pairs", exc)
