"""UBOE pair planning: auto-discover per-card uboe ip, only npu ranks are needed.

Mirrors the planning role of `planning/eid_pairs.py` but drives UBOE-specific device-side
discovery. Each pair is a client->server data flow over the ethernet parameter plane.

Two addresses per side:
  - execute_eid = the card's eid32 (reachable from host via system ssh, used to enter the
    device side to run ifconfig / urma_perftest);
  - bond_ip      = uboe ip (reachable only between devices, used for --sip/--dip/-S).

Both come from `uboe.discovery.collect_uboe_targets` unless `-bond-ip` overrides the uboe
ip. Cards without a bond/uboe ip cannot run UBOE traffic and are skipped.
"""

from sprinkler.auto.bandwidth.common import uboe_pair_result, parse_pair_spec, unique_ints
from sprinkler.auto.bandwidth.topology.rootinfo import RootInfoTopology
from sprinkler.auto.bandwidth.uboe.discovery import collect_uboe_targets
from sprinkler.base.ranges import parse_range

# Parameter-plane DMA channel per card (wiki command uses udmac0d1e3, the eth3 bond entry).
UBOE_DEFAULT_UDMA = "udmac0d1e3"


def parse_pair_spec_pairs(pair_spec):
    return parse_pair_spec(pair_spec)


def uboe_side(rank, target, machine="local", cpu_host=""):
    """One UBOE card side. `target` from collect_uboe_targets:
    execute_eid = login into the device (eid32), bond_ip = uboe ip (--sip/--dip/-S).
    `cpu_host` marks the side's cpu OS ("" or a local marker => local card; an ip => the
    peer machine hosting this card), which the show resolver / env pre-check use to pick
    the execution path (local system ssh vs peer paramiko + nested device ssh)."""
    return {"rank": rank, "target": f"NPU{rank}", "machine": machine,
            "cpu_host": cpu_host or "",
            "bond_ip": (target or {}).get("bond_ip", ""), "udma": UBOE_DEFAULT_UDMA,
            "execute_eid": (target or {}).get("execute_eid") or (target or {}).get("bond_ip", "")}


def build_uboe_pair(client_rank, server_rank, targets):
    client = targets.get(client_rank) or {}
    server = targets.get(server_rank) or {}
    missing = [rank for rank, target in ((client_rank, client), (server_rank, server))
               if not target.get("bond_ip")]
    ok = not missing
    reason = "bond ip missing" if missing else "ready"
    return uboe_pair_result(client=uboe_side(client_rank, client),
                            server=uboe_side(server_rank, server),
                            runner="npu", ok=ok, reason=reason,
                            execution_status="planned" if ok else "skipped")


def plan_uboe_pairs(args):
    """Return (ranks, pairs) for a UBOE run. Only npu ranks need to be specified."""
    targets = collect_uboe_targets(args)
    if not targets:
        raise ValueError("-urma-bandwidth -mode uboe: no UBOE targets discovered "
                         "(auto-discover needs the on-host card info; -bond-ip can override)")

    pair_spec = str(getattr(args, "pair_spec", "") or "").strip()
    if pair_spec:
        ranks = []
        pairs = []
        for client_rank, server_rank in parse_pair_spec_pairs(pair_spec):
            pairs.append(build_uboe_pair(client_rank, server_rank, targets))
            ranks.extend((client_rank, server_rank))
        return unique_ints(ranks), pairs

    rank_spec = str(getattr(args, "rank", "") or "").strip()
    ranks = parse_range(rank_spec) if rank_spec else sorted(targets)
    ranks = [rank for rank in ranks if rank in targets]
    # Mirror urma's intra d2d `-rank` expansion (`index_pairs`): EVERY rank in the range
    # takes part, honoring `-d2d-mode` (many-to-one / one-to-many / alltoall) and the
    # `-d2d-main` center. `-pair` stays explicit (a single client->server pair).
    d2d_mode = str(getattr(args, "d2d_mode", "many-to-one") or "many-to-one")
    master_rank = getattr(args, "d2d_main", None)
    client_server_pairs = RootInfoTopology.index_pairs(ranks, mode=d2d_mode, master_rank=master_rank)
    pairs = [build_uboe_pair(client, server, targets) for client, server in client_server_pairs]
    return unique_ints(ranks), pairs


def peer_cpu_targets(args):
    """Remote cpu OS targets from -ip / -node (`-mode uboe-xboard` requires -ip)."""
    from sprinkler.connect.targets import TargetContext
    context = TargetContext(args)
    return list(context.get_targets("cpu_os"))


def xboard_rank_pairs(clients, servers, same_plane, all2all, machine_index=0):
    """Mirror `ClusterPairBuilder.rank_pairs` mapping semantics for uboe-xboard.

    clients = peer machine's card ranks, servers = local card ranks. `machine_index`
    orders peer machines (only meaningful for all2all+same_plane).
    """
    if all2all and same_plane:
        if machine_index >= len(servers):
            return []
        server = servers[machine_index]
        return [(client, server) for client in clients or [server]]
    if all2all:
        return [(client, server) for server in servers for client in clients or [server]]
    if same_plane:
        return [(rank, rank) for rank in servers]
    server = servers[0] if servers else 0
    return [(client, server) for client in clients or [server]]


def build_uboe_xboard_pair(client_rank, server_rank, client_target, server_target,
                           client_machine, client_cpu_host):
    """One client->server uboe flow across two machines (client on the peer cpu)."""
    client_target = client_target or {}
    server_target = server_target or {}
    missing = [rank for rank, target in ((client_rank, client_target), (server_rank, server_target))
               if not (target or {}).get("bond_ip")]
    ok = not missing
    reason = "bond ip missing" if missing else "ready"
    client = uboe_side(client_rank, client_target, machine=client_machine or "peer",
                       cpu_host=client_cpu_host or "")
    if client_machine:
        client["target"] = f"{client_machine}:NPU{client_rank}"
    server = uboe_side(server_rank, server_target, machine="local", cpu_host="local")
    return uboe_pair_result(client=client, server=server, runner="npu", ok=ok, reason=reason,
                            execution_status="planned" if ok else "skipped")


def plan_uboe_xboard_pairs(args):
    """Plan (ranks, pairs) for `-urma-bandwidth -mode uboe-xboard`.

    Local cards are the server side; the peer machines (-ip) host the clients. Peer card
    targets are auto-discovered on the peer cpu (its /proc/asdrv_ub + per-device bond ip);
    a peer card that fails that discovery is skipped. Pairing mirrors xd2d: many-to-one
    by default (every peer rank hits local server0), adjustable via -xboard-pair,
    -server-rank, -all2all, -same-plane.
    """
    targets = collect_uboe_targets(args)
    if not targets:
        raise ValueError("-urma-bandwidth -mode uboe-xboard: no local UBOE targets discovered "
                         "(-bond-ip can override the uboe ip)")

    from sprinkler.connect.base import ResultProcessor
    from sprinkler.connect.ssh import SSHOSConnector
    from sprinkler.auto.bandwidth.uboe.remote import RemoteDeviceExecutor
    from sprinkler.auto.bandwidth.uboe.discovery import collect_remote_uboe_targets

    peer_targets = peer_cpu_targets(args)
    if not peer_targets:
        raise ValueError("-urma-bandwidth -mode uboe-xboard requires -ip <peer cpu ip>")
    manager = SSHOSConnector(args=args, result_processor=ResultProcessor())
    executor = RemoteDeviceExecutor(manager, peer_targets)
    peers = []
    try:
        for target in peer_targets:
            cpu_host = str(target.host or "")
            remote_host_targets = collect_remote_uboe_targets(
                executor, cpu_host,
                bond_ip_map=str(getattr(args, "bond_ip_map", "") or ""))
            peers.append((cpu_host, cpu_host, remote_host_targets))
    finally:
        executor.close()

    pair_spec = str(getattr(args, "xboard_pair_spec", "") or "").strip()
    if pair_spec:
        ranks, pairs = [], []
        for client_rank, server_rank in parse_pair_spec_pairs(pair_spec):
            client_cpu_host, client_machine, remote_targets = _peer_host_for_rank(peers, client_rank)
            pairs.append(build_uboe_xboard_pair(client_rank, server_rank, remote_targets.get(client_rank),
                                                targets.get(server_rank), client_machine, client_cpu_host))
            ranks.extend((client_rank, server_rank))
        return unique_ints(ranks), pairs

    rank_source = str(getattr(args, "server_rank", "") or "").strip() or \
        str(getattr(args, "rank", "") or "").strip()
    servers = parse_range(rank_source) if rank_source else sorted(targets)
    servers = [rank for rank in servers if rank in targets]
    if not servers:
        raise ValueError("-urma-bandwidth -mode uboe-xboard: no local (server) ranks "
                         f"available (discovered: {sorted(targets)})")

    same_plane = bool(getattr(args, "same_plane", False))
    all2all = bool(getattr(args, "all2all", False))
    ranks, pairs = [], []
    for machine_index, (client_cpu_host, client_machine, remote_targets) in enumerate(peers):
        clients = sorted(remote_targets)
        if not clients:
            continue
        for client_rank, server_rank in xboard_rank_pairs(clients, servers, same_plane, all2all,
                                                         machine_index):
            pairs.append(build_uboe_xboard_pair(client_rank, server_rank, remote_targets.get(client_rank),
                                                targets.get(server_rank), client_machine, client_cpu_host))
            ranks.extend((client_rank, server_rank))
    return unique_ints(ranks), pairs


def _peer_host_for_rank(peers, rank):
    for client_cpu_host, client_machine, remote_targets in peers:
        if rank in remote_targets:
            return client_cpu_host, client_machine, remote_targets
    return "", "", {}
