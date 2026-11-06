"""Parse rootinfo/topo data for NPU EID planning."""

import json
from dataclasses import dataclass
from pathlib import Path

from sprinkler.auto.bandwidth.common.route_conf import RouteConf, RouteEndpoint
from sprinkler.auto.bandwidth.common import unique_ints, int_value

@dataclass(frozen=True)
class RankEntry:
    rank: int
    device_id: int
    local_id: int
    group: int
    link0: str
    link1: str
    link2: str

class RootInfoTopology:
    PLANE_NAMES = {0: {"0", "l0", "link0", "plane0", "plane_pg0", "plane_pg_0"},
                   1: {"1", "l1", "link1", "plane1", "plane_pg1", "plane_pg_1"}}

    L2_PLANE_NAMES = {"2", "l2", "link2", "ubg", "plane_ubg", "supernode", "cluster"}

    def __init__(self, rootinfo_path, topo_path, entries, adjacency, groups, root_data=None, topo_data=None):
        self.rootinfo_path = Path(rootinfo_path).expanduser()
        self.topo_path = Path(topo_path).expanduser() if topo_path else None
        self.entries = dict(entries)
        self.adjacency = dict(adjacency)
        self.rank_groups = tuple(tuple(group) for group in groups)
        self._root_data = root_data
        self._topo_data = topo_data
        self._device_edges_cache = None
        self._port_eids_cache = None

    @classmethod
    def from_data(cls, root_data, rootinfo_path="<memory>", topo_data=None, topo_path=""):
        entries = cls.build_entries(root_data)
        adjacency = cls.build_adjacency(root_data, topo_data, entries) if topo_data else {}
        groups = cls.build_groups(topo_data or {}, entries)
        return cls(rootinfo_path, topo_path, entries, adjacency, groups,
                   root_data=root_data, topo_data=topo_data)

    @classmethod
    def load(cls, rootinfo_path):
        root_path = Path(rootinfo_path).expanduser()
        root_data = json.loads(root_path.read_text(encoding="utf-8", errors="ignore"))
        topo_text = cls.topo_file_path(root_data)
        topo_path = None
        if topo_text:
            path = Path(topo_text).expanduser()
            topo_path = path if path.is_absolute() else root_path.parent / path
        topo_data = json.loads(topo_path.read_text(encoding="utf-8", errors="ignore")) if topo_path else None
        return cls.from_data(root_data, root_path, topo_data=topo_data, topo_path=topo_path)

    def ranks(self):
        return sorted(self.entries)

    def groups(self):
        return [list(group) for group in self.rank_groups]

    def has_separate_groups(self):
        return len(self.rank_groups) > 1

    def group_of(self, rank):
        value = int(rank)
        return next((index for index, group in enumerate(self.rank_groups) if value in group), None)

    def same_group(self, client_rank, server_rank):
        client_group = self.group_of(client_rank)
        return client_group is not None and client_group == self.group_of(server_rank)

    def direct_pair(self, client_rank, server_rank):
        return bool(self.adjacency.get((int(client_rank), int(server_rank)))
                    and self.adjacency.get((int(server_rank), int(client_rank)))
                    and self.same_group(client_rank, server_rank))

    def same_plane_pairs(self, ranks):
        if len(self.rank_groups) < 2:
            return []
        selected_local_ids = {self.entries[rank].local_id for rank in unique_ints(ranks) if rank in self.entries}
        source = {self.entries[rank].local_id: rank for rank in self.rank_groups[0] if rank in self.entries}
        target = {self.entries[rank].local_id: rank for rank in self.rank_groups[1] if rank in self.entries}
        planes = sorted(selected_local_ids or set(source))
        return [(source[plane], target[plane]) for plane in planes if plane in source and plane in target]

    def endpoint(self, rank, link="L0"):
        entry = self.entries.get(int(rank))
        if entry is None:
            raise ValueError(f"rank missing in rootinfo: {rank}")
        link_name = str(link or "L0").upper()
        links = {"L0": entry.link0, "LINK0": entry.link0, "0": entry.link0,
                 "L1": entry.link1, "LINK1": entry.link1, "1": entry.link1,
                 "L2": entry.link2, "LINK2": entry.link2, "2": entry.link2,
                 "UBG": entry.link2, "SUPERNODE": entry.link2, "CLUSTER": entry.link2}
        eid_hex = links.get(link_name, entry.link0)
        return self.route_endpoint("NPU", rank, eid_hex), link_name

    def intra_d2d_pairs(self, ranks, mode="one-to-many", master_rank=None):
        pairs = []
        for src, dest in self.index_pairs(ranks, mode=mode, master_rank=master_rank):
            source = self.route_endpoint("NPU", src, self.adjacency.get((int(src), int(dest)), ""))
            destination = self.route_endpoint("NPU", dest, self.adjacency.get((int(dest), int(src)), ""))
            link = "topo" if source.eid32 and destination.eid32 else "no_topo_mapping"
            pairs.append((source, destination, link))
        return pairs

    @staticmethod
    def route_endpoint(role, rank, eid_hex):
        clean = RouteConf.clean_hex(eid_hex)
        return RouteEndpoint(role=role, rank=int(rank), eid_hex=clean, eid32=RouteConf.format_eid32(clean),
                             suffix4=RouteConf.suffix4(clean))

    @staticmethod
    def topo_file_path(root_data):
        return str(root_data.get("topo_file_path") or "").strip() if isinstance(root_data, dict) else ""

    @classmethod
    def build_entries(cls, root_data):
        entries = {}
        rank_items = [item for item in root_data.get("rank_list") or [] if isinstance(item, dict)]
        items = sorted(rank_items,
                       key=lambda item: (int_value(item.get("device_id"), 10**9),
                                         int_value(item.get("local_id"), 10**9)))
        layout = cls.rank_layout(items)
        width = max((local_id for _item, _group, local_id in layout), default=-1) + 1
        for index, (item, group, local_id) in enumerate(layout):
            device_id = int_value(item.get("device_id"), index)
            rank = group * max(width, 1) + local_id
            # 严格分层: xd2d(L0/L1) 只取 net_layer=1, ubg(L2) 只取 net_layer=2
            link0, link1 = cls.pick_links(cls.level_addrs(item, 1))
            link2 = cls.pick_layer2_link(cls.level_addrs(item, 2))
            entries[rank] = RankEntry(rank=rank, device_id=device_id, local_id=local_id, group=group,
                                      link0=link0, link1=link1, link2=link2)
        return entries

    @staticmethod
    def rank_layout(items):
        layout = []
        group = 0
        seen = set()
        previous = None
        for index, item in enumerate(items):
            local_id = int_value(item.get("local_id"), index)
            if seen and (local_id in seen or previous is not None and local_id <= previous):
                group += 1
                seen = set()
            layout.append((item, group, local_id))
            seen.add(local_id)
            previous = local_id
        return layout

    @staticmethod
    def level_addrs(rank, net_layer):
        level = next((level for level in rank.get("level_list") or []
                      if int_value(level.get("net_layer"), -1) == net_layer), {})
        return [addr for addr in level.get("rank_addr_list") or []
                if isinstance(addr, dict) and addr.get("addr")]

    @classmethod
    def pick_links(cls, addrs):
        by_plane = {}
        for addr in addrs:
            text = str(addr.get("plane_id") or "").strip().lower().replace("-", "_")
            parts = [part for part in text.split("_") if part]
            plane = None
            if text in cls.PLANE_NAMES[0]:
                plane = 0
            elif text in cls.PLANE_NAMES[1]:
                plane = 1
            elif parts and parts[-1] in {"0", "1"} and ("plane" in parts or "pg" in parts):
                plane = int(parts[-1])
            if plane in {0, 1}:
                by_plane[plane] = addr
        if by_plane:
            link0 = RouteConf.clean_hex((by_plane.get(0) or {}).get("addr"))
            link1 = RouteConf.clean_hex((by_plane.get(1) or {}).get("addr"))
            return link0, link1

        first = addrs[0] if addrs else {}
        second = addrs[1] if len(addrs) > 1 else {}
        if second and len(second.get("ports") or []) > len(first.get("ports") or []):
            first, second = second, first
        return RouteConf.clean_hex(first.get("addr")), RouteConf.clean_hex(second.get("addr"))

    @classmethod
    def pick_layer2_link(cls, addrs):
        for addr in addrs:
            plane = str(addr.get("plane_id") or "").strip().lower().replace("-", "_")
            if plane in cls.L2_PLANE_NAMES or "ubg" in plane:
                return RouteConf.clean_hex(addr.get("addr"))
        first = addrs[0] if addrs else {}
        return RouteConf.clean_hex(first.get("addr"))

    @classmethod
    def build_groups(cls, topo_data, entries):
        root_groups = {}
        for rank, entry in entries.items():
            root_groups.setdefault(entry.group, []).append(rank)
        root_groups = [sorted(group) for _index, group in sorted(root_groups.items())]
        if len(root_groups) <= 1:
            components = cls.topo_components(topo_data, entries)
            return components if sum(len(group) > 1 for group in components) > 1 else root_groups

        edges = cls.topo_rank_edges(topo_data, entries)
        for index, first in enumerate(root_groups):
            for second in root_groups[index + 1:]:
                cross = [(client, server) for client, server in edges
                         if ((client in first and server in second)
                             or (client in second and server in first))]
                first_seen = {rank for edge in cross for rank in edge if rank in first}
                second_seen = {rank for edge in cross for rank in edge if rank in second}
                if first_seen == set(first) and second_seen == set(second):
                    return [sorted(entries)]
        return root_groups

    @classmethod
    def topo_components(cls, topo_data, entries):
        edges = cls.topo_rank_edges(topo_data, entries)
        if not edges:
            return []
        nodes = set(entries)
        graph = {rank: set() for rank in nodes}
        for client, server in edges:
            graph[client].add(server)
            graph[server].add(client)
        groups = []
        while graph:
            pending = [next(iter(graph))]
            group = set()
            while pending:
                rank = pending.pop()
                if rank in group:
                    continue
                group.add(rank)
                pending.extend(graph.get(rank, ()))
            groups.append(sorted(group))
            graph = {rank: peers for rank, peers in graph.items() if rank not in group}
        return sorted(groups, key=lambda group: group[0])

    @classmethod
    def topo_rank_edges(cls, topo_data, entries):
        topo_to_root = cls.topo_to_root(topo_data, entries)
        root_rank = {entry.device_id: rank for rank, entry in entries.items()}
        edges = []
        for edge in topo_data.get("edge_list") or []:
            if not cls.is_device_edge(edge):
                continue
            local_a = int_value(edge.get("local_a"), None)
            local_b = int_value(edge.get("local_b"), None)
            device_a = topo_to_root.get(local_a)
            device_b = topo_to_root.get(local_b)
            if device_a in root_rank and device_b in root_rank:
                edges.append((root_rank[device_a], root_rank[device_b]))
        return edges

    @staticmethod
    def is_device_edge(edge):
        return (isinstance(edge, dict) and edge.get("link_type") == "PEER2PEER"
                and edge.get("position") == "DEVICE" and int_value(edge.get("net_layer"), 0) == 0)

    @classmethod
    def build_adjacency(cls, root_data, topo_data, entries):
        topo_to_root = cls.topo_to_root(topo_data, entries)
        root_rank = {entry.device_id: rank for rank, entry in entries.items()}
        # 严格分层: d2d 的 adjacency 只取 net_layer=0 的端口 EID
        port_eids = cls.port_eids(root_data, net_layer=0)
        adjacency = {}

        for edge in topo_data.get("edge_list") or []:
            if not cls.is_device_edge(edge):
                continue
            local_a = int_value(edge.get("local_a"), None)
            local_b = int_value(edge.get("local_b"), None)
            a = topo_to_root.get(local_a)
            b = topo_to_root.get(local_b)
            if a not in root_rank or b not in root_rank:
                continue
            for port in edge.get("local_a_ports") or []:
                eid = port_eids.get((a, str(port)))
                if eid:
                    adjacency[(root_rank[a], root_rank[b])] = RouteConf.format_eid32(eid)
            for port in edge.get("local_b_ports") or []:
                eid = port_eids.get((b, str(port)))
                if eid:
                    adjacency[(root_rank[b], root_rank[a])] = RouteConf.format_eid32(eid)
        return adjacency

    @classmethod
    def topo_to_root(cls, topo_data, entries):
        values = [int_value(item.get("device_id", item.get("local_id")), None)
                  for item in topo_data.get("device_list") or [] if isinstance(item, dict)]
        for item in topo_data.get("peer_list") or []:
            if isinstance(item, dict):
                for key in ("device_id", "local_id", "peer_id", "src_device_id", "dst_device_id"):
                    values.append(int_value(item.get(key), None))
            else:
                values.append(int_value(item, None))
        values.extend(int_value(edge.get(key), None) for edge in topo_data.get("edge_list") or []
                      if isinstance(edge, dict) for key in ("local_a", "local_b"))

        topo_devices = list(dict.fromkeys(value for value in values if value is not None))
        root_by_device = {entry.device_id: entry.device_id for entry in entries.values()}
        root_by_rank = {rank: entry.device_id for rank, entry in entries.items()}
        root_by_local = {entry.local_id: entry.device_id for entry in entries.values()}
        peer_list = [item for item in topo_data.get("peer_list") or [] if isinstance(item, dict)]
        logical_ids = bool(peer_list) and all("local_id" in item and "device_id" not in item for item in peer_list)
        if logical_ids:
            return {value: root_by_local[value] for value in topo_devices if value in root_by_local}
        return {value: root_by_device.get(value, root_by_rank.get(value))
                for value in topo_devices if value in root_by_device or value in root_by_rank}

    @classmethod
    def port_eids(cls, root_data, net_layer=None):
        out = {}
        for rank in root_data.get("rank_list") or []:
            if not isinstance(rank, dict):
                continue
            device_id = int_value(rank.get("device_id"), None)
            for level in rank.get("level_list") or []:
                if net_layer is not None and int_value(level.get("net_layer"), -1) != net_layer:
                    continue
                for addr in level.get("rank_addr_list") or []:
                    if not isinstance(addr, dict) or addr.get("addr_type") != "EID":
                        continue
                    eid = RouteConf.clean_hex(addr.get("addr"))
                    for port in addr.get("ports") or []:
                        out[(device_id, str(port))] = eid
        return out

    @staticmethod
    def index_pairs(ranks, mode="one-to-many", master_rank=None):
        selected = [int(rank) for rank in ranks]
        if len(selected) <= 1:
            return [(rank, rank) for rank in selected]

        text = str(mode or "one-to-many").replace("_", "-").lower()
        if text in {"alltoall", "fullmesh"}:  # fullmesh 为旧名称，保留兼容
            return [(src, dest) for src in selected for dest in selected if src != dest]
        if text not in {"one-to-many", "many-to-one"}:
            raise ValueError(f"unsupported d2d mode: {mode}")

        center_rank = int(master_rank) if master_rank is not None else selected[0]
        if center_rank not in selected:
            raise ValueError(f"d2d main rank {center_rank} is not selected")
        if text == "many-to-one":
            return [(rank, center_rank) for rank in selected if rank != center_rank]
        return [(center_rank, rank) for rank in selected if rank != center_rank]

    def device_edges(self):
        if self._device_edges_cache is None:
            topo_data = self._topo_data or {}
            self._device_edges_cache = [edge for edge in topo_data.get("edge_list") or []
                                        if self.is_device_edge(edge)]
        return self._device_edges_cache

    def edge_eid_map(self):
        """严格两步: 查 topo DEVICE 边(step1: 端口), 再从 rootinfo 按端口匹配 EID(step2).
        返回 {(local_a|local_b): {"a": {"local","ports","eid"}, "b": {...}}}.
        对没有对应 rootinfo 项的端点, eid 为空, 由另一端机器自行解析."""
        if self._port_eids_cache is None:
            self._port_eids_cache = self.port_eids(self._root_data or {}, net_layer=0)
        local_to_device = {entry.local_id: entry.device_id for entry in self.entries.values()}
        edges = {}
        for edge in self.device_edges():
            a = int_value(edge.get("local_a"), None)
            b = int_value(edge.get("local_b"), None)
            if a is None or b is None:
                continue
            edges[f"{a}|{b}"] = {"a": self._edge_slot(a, edge.get("local_a_ports") or [], local_to_device),
                                 "b": self._edge_slot(b, edge.get("local_b_ports") or [], local_to_device)}
        return edges

    def _edge_slot(self, local, ports, local_to_device):
        device = local_to_device.get(local)
        eid = ""
        if device is not None:
            for port in ports:
                value = self._port_eids_cache.get((device, str(port)))
                if value:
                    eid = RouteConf.clean_hex(value)
                    break
        return {"local": local, "ports": [str(port) for port in ports], "eid": eid}
