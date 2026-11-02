"""Cluster inventory builder for EID pair planning."""

import json
import posixpath
import shlex
from contextlib import closing

from sprinkler.auto.bandwidth.common import unique_ints, int_value, pair_result, xboard_pair_result, link_names, xboard_mapping_mode, DEFAULT_ROOTINFO, _is_ipv4
from sprinkler.auto.bandwidth.topology.rootinfo import RootInfoTopology
from sprinkler.auto.bandwidth.common.route_conf import RouteConf
from sprinkler.connect.targets import ConnectionTarget
from sprinkler.config.host_device_pairs import get_host_and_device_pairs, parse_host_device_pairs_text

def remote_inventory_parts(remote_plan):
    return (remote_plan.get("local_inventory") or remote_plan.get("server_inventory") or {},
            remote_plan.get("target_inventories") or remote_plan.get("client_inventories") or [],
            [dict(item) for item in remote_plan.get("inventory_errors") or [] if isinstance(item, dict)])

def local_xboard_pairs(ranks, link_mode="all", same_plane=False, all2all=False, d2d_mode="one-to-many",
                       master_rank=None, server_ranks=None, force_xboard=False):
    topology = RootInfoTopology.load(DEFAULT_ROOTINFO)
    inventory = ClusterInventoryBuilder().from_topology(None, topology, get_host_and_device_pairs())
    return local_xboard_pairs_from_inventory(topology, inventory, ranks, link_mode, same_plane, all2all,
                                             d2d_mode, master_rank, server_ranks, force_xboard=force_xboard)

def local_xboard_pairs_from_inventory(topology, inventory, ranks, link_mode="all", same_plane=False, all2all=False,
                                      d2d_mode="one-to-many", master_rank=None, server_ranks=None,
                                      force_xboard=False):
    supernode = str(link_mode or "").strip().lower() == "ubg"
    available = {int(item["rank"]) for item in inventory.get("ranks") or []}
    requested = unique_ints(ranks) or topology.ranks()
    clients = [rank for rank in requested if rank in available]
    servers = [rank for rank in unique_ints(server_ranks or []) if rank in available]
    if not clients or server_ranks is not None and not servers:
        return []
    if same_plane:
        rank_pairs = topology.same_plane_pairs(clients)
    elif servers and all2all:
        rank_pairs = [(client, server) for client in clients for server in servers]
    elif all2all:
        rank_pairs = [(client, server) for client in clients for server in clients if client != server]
    elif servers:
        rank_pairs = [(client, servers[0]) for client in clients]
    else:
        rank_pairs = RootInfoTopology.index_pairs(clients, d2d_mode, master_rank)

    pairs = []
    mapping_mode = (xboard_mapping_mode(same_plane=True, all2all=all2all)
                    if same_plane else str(d2d_mode or "one-to-many"))
    force = bool(force_xboard) or server_ranks is not None or same_plane or all2all
    builder = ClusterPairBuilder(inventory, [inventory], edge_strict=not force)
    for client_rank_value, server_rank_value in rank_pairs:
        if client_rank_value not in available or server_rank_value not in available:
            continue
        if not supernode and not force and topology.direct_pair(client_rank_value, server_rank_value):
            pairs.append(local_topo_pair(topology, inventory, client_rank_value, server_rank_value, mapping_mode))
            continue
        client_rank = builder.rank_info(inventory, client_rank_value)
        server_rank = builder.rank_info(inventory, server_rank_value)
        links = builder.selected_links(client_rank, server_rank, link_mode)
        pairs.extend(builder.pair("local", 0, client_rank_value, server_rank_value, link, inventory,
                                  client_rank, server_rank, mapping_mode) for link in links)
    return pairs

def local_topo_pair(topology, inventory, client_rank, server_rank, mapping_mode):
    source = topology.route_endpoint("NPU", client_rank, topology.adjacency.get((client_rank, server_rank), ""))
    target = topology.route_endpoint("NPU", server_rank, topology.adjacency.get((server_rank, client_rank), ""))
    rank_items = {int(item["rank"]): item for item in inventory.get("ranks") or []}
    client_item = rank_items.get(client_rank, {})
    server_item = rank_items.get(server_rank, {})
    client = {"rank": client_rank, "target": f"NPU{client_rank}", "eid32": source.eid32,
              "execute_eid": client_item.get("execute_eid", "")}
    server = {"rank": server_rank, "target": f"NPU{server_rank}", "eid32": target.eid32,
              "execute_eid": server_item.get("execute_eid", "")}
    ok = bool(source.eid32 and target.eid32)
    reason = "ready" if ok else "no_topo_mapping"
    return pair_result("intra_d2d", "intra", "npu", client=client, server=server,
                       link="topo" if ok else "no_topo_mapping", ok=ok, reason=reason,
                       mapping_mode=mapping_mode, execution_status="planned" if ok else "skipped")

class ClusterInventoryBuilder:
    def from_text(self, target, rootinfo_text, topo_text="", host_device_pairs=None):
        root_data = json.loads(str(rootinfo_text or "{}"))
        topo_data = json.loads(str(topo_text or "{}")) if str(topo_text or "").strip() else None
        return self.from_topology(target, RootInfoTopology.from_data(root_data, topo_data=topo_data),
                                  host_device_pairs=host_device_pairs)

    def from_topology(self, target, topology, host_device_pairs=None):
        target = ConnectionTarget.from_value(target) if target is not None else None
        return {"machine": target.label() if target else "local",
                "host": target.host if target else "local", "target": target.to_dict() if target else {},
                "groups": topology.groups(), "edge_eids": topology.edge_eid_map(),
                "ranks": [self.rank_item(rank, topology, host_device_pairs) for rank in topology.ranks()]}

    def rank_item(self, rank, topology, host_device_pairs=None):
        endpoints = {name: topology.endpoint(rank, link=name)[0] for name in ("L0", "L1", "L2")}
        links = {name: {"eid_hex": item.eid_hex, "eid32": item.eid32, "suffix4": item.suffix4}
                 for name, item in endpoints.items()}
        entry = topology.entries.get(int(rank))
        values = self.execute_values(host_device_pairs, int(rank), entry.device_id if entry else None)
        execute_eid = RouteConf.format_eid32(values[1]) if len(values) > 1 else ""
        # EID不可用但静态配置是合法IPv4时, 用IPv4作为执行地址(供ssh免密)
        if not execute_eid and len(values) > 1 and _is_ipv4(str(values[1])):
            execute_eid = str(values[1]).strip()
        return {"rank": int(rank), "device_id": entry.device_id if entry else int(rank),
                "local_id": entry.local_id if entry else int(rank), "group": entry.group if entry else 0,
                "execute_eid": execute_eid, "links": links}

    def execute_values(self, host_device_pairs, rank, device_id):
        pairs = host_device_pairs or {}
        for key in (rank, device_id):
            if key is not None and key in pairs:
                return pairs[key]
        return []

class ClusterPairBuilder:
    def __init__(self, server_inventory, client_inventories, edge_strict=True):
        self.server_inventory = server_inventory or {}
        self.client_inventories = list(client_inventories or [])
        self.edge_strict = edge_strict

    def xboard_pairs(self, ranks, link_mode="all", same_plane=False, all2all=False):
        pairs = []
        mapping_mode = xboard_mapping_mode(same_plane=same_plane, all2all=all2all)
        for index, client_inventory in enumerate(self.client_inventories):
            client_machine = str(client_inventory.get("client_machine") or client_inventory.get("machine")
                                 or f"C{index}").strip()
            for client_rank_value, server_rank_value in self.rank_pairs(client_inventory, ranks, same_plane,
                                                                        all2all, index):
                client_rank = self.rank_info(client_inventory, client_rank_value)
                server_rank = self.rank_info(self.server_inventory, server_rank_value)
                missing_rank = [name for name, value in (("client_rank", client_rank), ("server_rank", server_rank))
                                if not value]
                links = ("",) if missing_rank else self.selected_links(client_rank, server_rank, link_mode)
                for link in links:
                    pairs.append(self.pair(client_machine, index, client_rank_value, server_rank_value, link,
                                           client_inventory, client_rank, server_rank, mapping_mode, missing_rank))
        return pairs

    def rank_pairs(self, client_inventory, server_ranks, same_plane, all2all, client_index):
        servers = [int(rank) for rank in server_ranks]
        clients = unique_ints(item.get("rank") for item in client_inventory.get("ranks") or [])
        if all2all and same_plane:
            if client_index >= len(servers):
                return []
            server = servers[client_index]
            return [(client, server) for client in clients or [server]]
        if all2all:
            return [(client, server) for server in servers for client in clients or [server]]
        if same_plane:
            return [(rank, rank) for rank in servers]
        server = servers[0] if servers else 0
        return [(client, server) for client in clients or [server]]

    def selected_links(self, client_rank, server_rank, link_mode):
        requested = link_names(link_mode)
        mode = str(link_mode or "all").strip().lower()
        if mode != "all":
            return requested
        if self.edge_strict and self.strict_edge_pair(int_value(client_rank.get("rank")), int_value(server_rank.get("rank"))):
            return ("L0",)
        common = [link for link in requested if all(self.link_info(rank, link).get("eid32")
                  for rank in (client_rank, server_rank))]
        return tuple(common) if common else requested[:1]

    def strict_edge_pair(self, client_rank, server_rank):
        """严格两步: 查 topo 边端口 -> 按端口从 rootinfo 取 EID.
        返回 (client_eid32, server_eid32); 无法按边解析时返回 None."""
        client_rank = int_value(client_rank)
        server_rank = int_value(server_rank)
        if client_rank is None or server_rank is None:
            return None
        client_inventory = self._inventory_of_rank(client_rank)
        server_inventory = self._inventory_of_rank(server_rank)
        if not client_inventory or not server_inventory:
            return None
        client_info = self.rank_info(client_inventory, client_rank)
        server_info = self.rank_info(server_inventory, server_rank)
        client_local = int_value(client_info.get("local_id"))
        server_local = int_value(server_info.get("local_id"))
        if client_local is None or server_local is None:
            return None
        client_edges = client_inventory.get("edge_eids") or {}
        server_edges = server_inventory.get("edge_eids") or {}
        for key in (f"{client_local}|{server_local}", f"{server_local}|{client_local}"):
            client_slot = self._edge_slot(client_edges.get(key), client_local)
            server_slot = self._edge_slot(server_edges.get(key), server_local)
            if not client_slot or not server_slot:
                continue
            client_eid32 = RouteConf.format_eid32(client_slot.get("eid", ""))
            server_eid32 = RouteConf.format_eid32(server_slot.get("eid", ""))
            if client_eid32 and server_eid32:
                return client_eid32, server_eid32
        return None

    def _inventory_of_rank(self, rank):
        inventories = [self.server_inventory, *(self.client_inventories or [])]
        for inventory in inventories:
            if self.rank_info(inventory, rank):
                return inventory
        return None

    @staticmethod
    def _edge_slot(edge, local):
        if not edge:
            return None
        for side in ("a", "b"):
            if int_value(edge.get(side, {}).get("local")) == int_value(local):
                return edge.get(side)
        return None

    def pair(self, client_machine, client_index, client_rank_value, server_rank_value, link, client_inventory,
             client_rank, server_rank, mapping_mode, missing=None):
        strict = self.strict_edge_pair(client_rank_value, server_rank_value) \
            if self.edge_strict and str(link or "").upper() in {"", "0", "L0", "LINK0", "ALL"} else None
        if strict:
            client_eid32, server_eid32 = strict
            client_link = {"eid_hex": RouteConf.clean_hex(client_eid32), "eid32": client_eid32,
                           "suffix4": RouteConf.suffix4(client_eid32)}
            server_link = {"eid_hex": RouteConf.clean_hex(server_eid32), "eid32": server_eid32,
                           "suffix4": RouteConf.suffix4(server_eid32)}
        else:
            client_link = self.link_info(client_rank, link)
            server_link = self.link_info(server_rank, link)
        server_machine = str(self.server_inventory.get("machine") or "local")
        client = self.pair_side(client_inventory, client_machine, client_rank_value, client_rank, client_link)
        server = self.pair_side(self.server_inventory, server_machine, server_rank_value, server_rank, server_link)
        missing = list(missing or []) or [name for name, value in (("client_eid32", client_link.get("eid32")),
                                      ("server_eid32", server_link.get("eid32"))) if not value]
        ok = not missing
        link_label = f"xboard:{link}" if link else "xboard:missing"
        return xboard_pair_result(client=client, server=server, link=link_label,
                                  ok=ok, reason="ready" if ok else f"missing {','.join(missing)}",
                                  missing_fields=",".join(missing), cmd="cluster-bw inventory",
                                  target=f"{client['target']}->{server['target']}", host=client["cpu_host"],
                                  mapping_mode=mapping_mode, client_machine_index=client_index)

    def pair_side(self, inventory, machine, rank, rank_item=None, link_info=None):
        target = inventory.get("target") or {}
        cpu_target = str(target.get("name") or inventory.get("machine") or "")
        return {"rank": rank, "machine": machine, "target": f"{machine}:NPU{rank}",
                "eid32": (link_info or {}).get("eid32", ""), "cpu_target": cpu_target,
                "cpu_host": str(inventory.get("host") or target.get("host") or ""),
                "execute_eid": (rank_item or {}).get("execute_eid", "")}

    def rank_info(self, inventory, rank):
        expected = int_value(rank)
        return next((item for item in inventory.get("ranks") or [] if int_value(item.get("rank")) == expected), {})

    def link_info(self, rank_item, link):
        links = rank_item.get("links") or {}
        return links.get(link) or {}

class ClusterInventoryCollector:
    def __init__(self, args, connection_manager):
        self.connection_manager = connection_manager
        self.builder = ClusterInventoryBuilder()
        self.rootinfo = DEFAULT_ROOTINFO

    def collect(self, local_client, local, targets):
        local_inventory = self.collect_with_client(local_client, local)
        return self.collect_result(local_inventory, targets, "ssh")

    def collect_command_host(self, targets):
        topology = RootInfoTopology.load(self.rootinfo)
        local_inventory = self.builder.from_topology(None, topology, self.local_host_device_pairs())
        return self.collect_result(local_inventory, targets, "local+ssh")

    def collect_result(self, local_inventory, targets, source):
        target_inventories, inventory_errors = self.collect_targets(targets)
        return {"local_inventory": local_inventory, "target_inventories": target_inventories,
                "inventory_errors": inventory_errors, "cluster_inventory_source": source}

    def collect_targets(self, targets):
        inventories = []
        errors = []
        for target in targets:
            try:
                with closing(self.connection_manager.connect_client(target)) as client:
                    inventories.append(self.collect_with_client(client, target))
            except Exception as exc:
                errors.append(self.inventory_error(target, exc))
        return inventories, errors

    def inventory_error(self, target, exc):
        target = ConnectionTarget.from_value(target)
        reason = str(exc) or "inventory failed"
        error_type = reason.split(":", 1)[0].strip() if ":" in reason else "inventory"
        label = target.label()
        client = {"machine": label, "target": label, "cpu_target": label, "cpu_host": target.host}
        server = {"machine": "local", "target": "local", "cpu_target": "local", "cpu_host": "local"}
        return pair_result("xboard_inventory_error", "inventory", "collector", client=client, server=server,
                           ok=False, rc=255, reason=reason, error_type=error_type or "inventory",
                           missing_fields="inventory", cmd="cluster inventory", target=f"inventory:{label}",
                           host=target.host, port=target.port, backend="collector", stderr=reason)

    def collect_with_client(self, client, target):
        rootinfo_text = self.read_file(client, target, self.rootinfo)
        topo_path = self.remote_topo_path(rootinfo_text)
        topo_text = self.read_file(client, target, topo_path) if topo_path else ""
        root_data = json.loads(str(rootinfo_text or "{}"))
        device_ids = unique_ints(item.get("device_id") for item in root_data.get("rank_list") or []
                                 if isinstance(item, dict))
        host_device_pairs = self.read_host_device_pairs(client, target, device_ids)
        return self.builder.from_text(target, rootinfo_text, topo_text=topo_text, host_device_pairs=host_device_pairs)

    def remote_topo_path(self, rootinfo_text):
        topo_path = RootInfoTopology.topo_file_path(json.loads(str(rootinfo_text or "{}")))
        if not topo_path or topo_path.startswith("/"):
            return topo_path
        return posixpath.join(posixpath.dirname(self.rootinfo), topo_path)

    def read_file(self, client, target, path):
        command = f"cat {shlex.quote(path)}"
        result = self.connection_manager.run_command(client, target, command)
        if not result.get("ok"):
            error = result.get("stderr") or result.get("stdout")
            raise RuntimeError(f"cluster inventory read failed on {target.label()}: {path}: {error}")
        return result.get("stdout") or ""

    def read_host_device_pairs(self, client, target, device_ids):
        devices = " ".join(str(device) for device in device_ids)
        command = (f"for dev in {devices}; do "
                   "echo $dev > /proc/asdrv_ub/dev_id 2>/dev/null || continue; "
                   "echo __SPRINKLER_DEV__ $dev; "
                   "cat /proc/asdrv_ub/pair_info 2>/dev/null || true; done; true")
        result = self.connection_manager.run_command(client, target, command)
        pairs = parse_host_device_pairs_text(result.get("stdout")) if result.get("ok") else {}
        # 远端无/proc/asdrv_ub时, 用本地静态host/device对(两端NPU IP相同)。
        # 键0-3与rank/device_id一致, rank_item的execute_eid据此取到NPU的IP/地址。
        if not pairs:
            try:
                pairs = get_host_and_device_pairs()
            except Exception:
                pairs = {}
        return pairs

    def local_host_device_pairs(self):
        try:
            return get_host_and_device_pairs()
        except Exception:
            return {}
