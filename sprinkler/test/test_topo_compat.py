"""A5/A6 topology compatibility tests for RootInfoTopology.

A5(Atlas 850) topo.json 用 net_layer=0 + link_type=PEER2PEER 描述 NPU 间直连；
A6(Atlas 860) 改为 net_layer=0 + link_type=PEER2NET + topo_type=CLOS，
边只有 local_a（NPU 接入 CLOS 网络的端口），网络内两两互通。
这两个用例保证 A6 能正确解析 d2d EID，同时 A5 行为不变。
"""

import unittest

from sprinkler.auto.bandwidth.common.route_conf import RouteConf
from sprinkler.auto.bandwidth.topology.inventory import (
    ClusterInventoryBuilder,
    ClusterPairBuilder,
    local_xboard_pairs_from_inventory,
)
from sprinkler.auto.bandwidth.topology.rootinfo import RootInfoTopology


def eid_hex(seed):
    return f"{seed:032x}"


def eid32(seed):
    return RouteConf.format_eid32(eid_hex(seed))


def a6_topo(net_layers=1):
    edges = []
    for local in range(8):
        for net_layer in range(net_layers):
            edges.append({
                "net_layer": net_layer,
                "link_type": "PEER2NET",
                "topo_type": "CLOS",
                "topo_instance_id": 0,
                "topo_attr": "",
                "local_a": local,
                "local_a_ports": [f"{net_layer}/{port}" for port in range(8)],
                "protocols": ["UB_CTP", "UB_MEM"],
                "position": "DEVICE",
            })
    return {
        "version": "2.0",
        "hardware_type": "Atlas 860",
        "peer_count": 8,
        "peer_list": [{"local_id": local} for local in range(8)],
        "edge_count": len(edges),
        "edge_list": edges,
    }


def a6_rootinfo(seed_base=0x1000, local_id_func=None):
    ranks = []
    for device in range(8):
        local_id = local_id_func(device) if local_id_func else device
        addrs = [
            {"addr_type": "EID", "addr": eid_hex(seed_base * (device + 1) + port),
             "ports": [f"0/{port}"]}
            for port in range(8)
        ]
        ranks.append({"device_id": device, "local_id": local_id,
                      "level_list": [{"net_layer": 0, "rank_addr_list": addrs}]})
    return {"version": "2.0", "rank_list": ranks}


def a5_rootinfo():
    ranks = []
    for device in range(8):
        d2d_addrs = [{"addr_type": "EID", "addr": eid_hex(0x2000 * (device + 1)),
                      "ports": [f"0/{port}" for port in range(8)]}]
        plane_addrs = [
            {"addr_type": "EID", "addr": eid_hex(0x3000 * (device + 1) + plane),
             "ports": [f"1/{plane}"], "plane_id": f"plane{plane}"}
            for plane in range(2)
        ]
        ranks.append({"device_id": device, "local_id": device, "level_list": [
            {"net_layer": 0, "rank_addr_list": d2d_addrs},
            {"net_layer": 1, "rank_addr_list": plane_addrs},
        ]})
    return {"version": "2.0", "rank_list": ranks}


def a5_topo():
    edges = []
    for first in range(8):
        for second in range(first + 1, 8):
            edges.append({
                "net_layer": 0,
                "link_type": "PEER2PEER",
                "topo_type": "CLOS",
                "topo_instance_id": 0,
                "local_a": first,
                "local_b": second,
                "local_a_ports": [f"0/{first}"],
                "local_b_ports": [f"0/{second}"],
                "position": "DEVICE",
            })
    return {"version": "2.0", "hardware_type": "Atlas 850", "edge_list": edges}


def build(root_data, topo_data):
    topology = RootInfoTopology.from_data(root_data, topo_data=topo_data)
    inventory = ClusterInventoryBuilder().from_topology(None, topology, {})
    return topology, inventory


class A6Peer2NetTest(unittest.TestCase):
    def test_clos_builds_full_mesh_adjacency(self):
        topology, _inventory = build(a6_rootinfo(), a6_topo())
        for client in range(8):
            for server in range(8):
                if client == server:
                    self.assertIsNone(topology.adjacency.get((client, server)))
                    continue
                self.assertEqual(eid32(0x1000 * (client + 1)), topology.adjacency[(client, server)])

    def test_d2d_main_pair_is_ready(self):
        topology, inventory = build(a6_rootinfo(), a6_topo())
        # 默认 -d2d-mode many-to-one: 其余 rank -> main(0)
        pairs = local_xboard_pairs_from_inventory(
            topology, inventory, [0, 1], link_mode="all",
            d2d_mode="many-to-one", master_rank=0)
        self.assertEqual(1, len(pairs))
        pair = pairs[0]
        self.assertEqual("intra_d2d", pair["kind"])
        self.assertEqual("topo", pair["link"])
        self.assertTrue(pair["ok"], pair["reason"])
        self.assertEqual(eid32(0x1000 * 2), pair["client_eid32"])
        self.assertEqual(eid32(0x1000), pair["server_eid32"])
        self.assertEqual((1, 0), (pair["client_rank"], pair["server_rank"]))

    def test_strict_edge_pair_uses_net_ports(self):
        topology, inventory = build(a6_rootinfo(), a6_topo())
        builder = ClusterPairBuilder(inventory, [inventory])
        self.assertEqual((eid32(0x1000), eid32(0x2000)), builder.strict_edge_pair(0, 1))
        self.assertEqual((eid32(0x2000), eid32(0x1000)), builder.strict_edge_pair(1, 0))
        self.assertIn("0|1", topology.edge_eid_map())

    def test_first_resolvable_port_wins(self):
        root_data = a6_rootinfo()
        # 第一个端口没有 EID 时, 退到下一个端口
        del root_data["rank_list"][0]["level_list"][0]["rank_addr_list"][0]
        topology, inventory = build(root_data, a6_topo())
        self.assertEqual(eid32(0x1000 + 1), topology.adjacency[(0, 1)])
        builder = ClusterPairBuilder(inventory, [inventory])
        self.assertEqual((eid32(0x1000 + 1), eid32(0x2000)), builder.strict_edge_pair(0, 1))

    def test_peer2net_does_not_merge_rank_groups(self):
        # 两个 board(各 4 NPU)时, A6 的 d2d PEER2NET 边不能把 group 合并,
        # 否则 -same-plane / ubg 会拿不到分组.
        topology, _inventory = build(a6_rootinfo(local_id_func=lambda device: device % 4), a6_topo())
        self.assertEqual([[0, 1, 2, 3], [4, 5, 6, 7]], topology.groups())
        self.assertEqual([], RootInfoTopology.topo_rank_edges(a6_topo(), topology.entries))
        self.assertEqual([(0, 4), (1, 5), (2, 6), (3, 7)], topology.same_plane_pairs(range(8)))

    def test_non_d2d_net_layers_are_ignored(self):
        topology, _inventory = build(a6_rootinfo(), a6_topo(net_layers=3))
        self.assertEqual(8, len(topology.device_edges()))
        for edge in topology.device_edges():
            self.assertEqual(0, edge["net_layer"])


class A5Peer2PeerTest(unittest.TestCase):
    def test_direct_edges_unchanged(self):
        topology, inventory = build(a5_rootinfo(), a5_topo())
        self.assertEqual(eid32(0x2000), topology.adjacency[(0, 1)])
        self.assertEqual(eid32(0x4000), topology.adjacency[(1, 0)])
        pairs = local_xboard_pairs_from_inventory(
            topology, inventory, [0, 1], link_mode="all",
            d2d_mode="many-to-one", master_rank=0)
        self.assertEqual(1, len(pairs))
        self.assertEqual("intra_d2d", pairs[0]["kind"])
        self.assertTrue(pairs[0]["ok"], pairs[0]["reason"])

    def test_rank_links_still_use_net_layer_1(self):
        topology, inventory = build(a5_rootinfo(), a5_topo())
        links = inventory["ranks"][0]["links"]
        self.assertEqual(eid32(0x3000), links["L0"]["eid32"])
        self.assertEqual(eid32(0x3001), links["L1"]["eid32"])

    def test_without_topo_strict_edge_pair_stays_empty(self):
        _topology, inventory = build(a5_rootinfo(), None)
        builder = ClusterPairBuilder(inventory, [inventory])
        self.assertIsNone(builder.strict_edge_pair(0, 1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
