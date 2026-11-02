"""Pair construction, validation, and result utilities."""

from sprinkler.auto.bandwidth.common.utils import (
    PAIR_DEFAULTS, INTRA_MODES, XBOARD_MODES, int_value, unique_ints, text_value,
    PAIR_CONTEXT_FIELDS, XBOARD_PAIR_REQUIRED_FIELDS,
    pair_matches_link_mode, normalize_link_mode,
)

def pair_result(kind, scope, runner, client=None, server=None, ok=True, reason="", rc=None, error_type="", **extra):
    result = {"kind": kind, "scope": scope, "runner": runner, **PAIR_DEFAULTS,
              **{f"client_{name}": value for name, value in (client or {}).items() if value is not None},
              **{f"server_{name}": value for name, value in (server or {}).items() if value is not None}}
    result.update(ok=bool(ok),
                  rc=(0 if ok else 2) if rc is None else rc,
                  reason=reason or ("ready" if ok else "failed"),
                  error_type=error_type or ("" if ok else "config"))
    result.update({name: value for name, value in extra.items() if value is not None})
    if not result["target"]:
        result["target"] = f"{result['client_target']}->{result['server_target']}"
    return result

def xboard_pair_result(client=None, server=None, runner="npu", link="", ok=True, reason="", **extra):
    return pair_result("xboard_d2d", "xboard", runner, client=client, server=server,
                       link=link, ok=ok, reason=reason, **extra)

def uboe_pair_result(client=None, server=None, runner="npu", ok=True, reason="", **extra):
    return pair_result("uboe_d2d", "uboe", runner, client=client, server=server,
                       ok=ok, reason=reason, **extra)

def parse_bond_ip_map(text):
    """Parse a -bond-ip map like '0=10.10.25.2,1=10.10.32.2' or 'npu0=...,npu1=...' -> {rank: ip}."""
    result = {}
    if not text:
        return result
    for part in str(text).strip().split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"Invalid bond-ip entry: '{part}', expected rank=ip")
        rank_text, ip = (piece.strip() for piece in part.split("=", 1))
        rank_text = rank_text.lower()
        if rank_text.startswith("npu"):
            rank_text = rank_text[3:]
        if not rank_text.isdecimal():
            raise ValueError(f"Invalid bond-ip rank: '{rank_text}'")
        result[int(rank_text)] = ip
    return result

def pair_context(pair, kind_field="pair_kind"):
    result = {field: pair.get(field, "") for field in PAIR_CONTEXT_FIELDS}
    if kind_field:
        result[kind_field] = pair.get("kind", "")
    return result

def pair_target(pair):
    return f"{pair.get('kind')}:{pair.get('client_target')}->{pair.get('server_target')}"

def compact_target_text(item):
    client = pair_side_text(item, "client")
    server = pair_side_text(item, "server")
    if client and server:
        return f"{client}->{server}"
    target = str((item or {}).get("target") or "").strip()
    return target.split(":", 1)[1] if ":" in target else target

def pair_side_text(item, role, include_eid=False):
    target = str((item or {}).get(f"{role}_target") or "").strip()
    kind = str((item or {}).get("pair_kind") or (item or {}).get("kind") or "").lower()
    if not kind:
        kind = str((item or {}).get("target") or "").partition(":")[0].lower()
    if kind in {"intra_h2d", "intra_d2h"} and target.upper().startswith("CPU") and target[3:].isdigit():
        target = "CPU"
    if not include_eid:
        return target
    eid = str((item or {}).get(f"{role}_eid32") or "NA").strip() or "NA"
    return f"{target}[{eid}]" if target else f"[{eid}]"

def pair_flow_text(item, include_eid=False):
    return f"{pair_side_text(item, 'client', include_eid)} ⇄ {pair_side_text(item, 'server', include_eid)}"

def is_xboard_pair(pair):
    return isinstance(pair, dict) and (pair.get("kind") == "xboard_d2d" or pair.get("scope") == "xboard")

def missing_xboard_fields(pair, fields=None):
    if fields is None:
        fields = XBOARD_PAIR_REQUIRED_FIELDS
    return [] if not is_xboard_pair(pair) else [field for field in fields if pair.get(field) in ("", None)]

def selected_xboard_pairs(pairs, ranks=None, link_mode="all", clients=None, same_plane=False, all2all=False):
    selected = unique_ints(ranks)
    client_order = [str(client).strip().upper() for client in clients or [] if str(client).strip()]
    if all2all and same_plane and not client_order:
        client_order = list(dict.fromkeys(client_machine(pair) for pair in pairs if client_machine(pair)))
    client_set = set(client_order)
    return [pair for pair in pairs if is_xboard_pair(pair) and pair_matches_link_mode(pair, link_mode)
            and (not client_set or client_machine(pair) in client_set)
            and xboard_rank_selected(pair, selected, client_order, same_plane, all2all)]

def xboard_rank_selected(pair, selected, client_order, same_plane, all2all):
    client_rank = int_value(pair.get("client_rank"))
    server_rank = int_value(pair.get("server_rank"))
    if all2all and same_plane:
        if not selected:
            return True
        machine = client_machine(pair)
        if machine not in client_order:
            return False
        index = client_order.index(machine)
        return index < len(selected) and server_rank == selected[index]
    if all2all:
        return not selected or server_rank in selected
    if same_plane:
        return (not selected or server_rank in selected) and client_rank == server_rank
    first = selected[:1]
    return not first or server_rank in first

def parse_pair_spec(text):
    if not text or not str(text).strip():
        return []
    text = str(text).strip()
    pairs = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" not in part:
            raise ValueError(f"Invalid pair spec part: {part}, expected format: rank-rank")
        rank_parts = part.split("-")
        if len(rank_parts) != 2:
            raise ValueError(f"Invalid pair spec part: {part}, expected exactly one dash separator")
        try:
            client_rank = int(rank_parts[0].strip())
            server_rank = int(rank_parts[1].strip())
        except ValueError as exc:
            raise ValueError(f"Invalid rank in pair spec: {part}") from exc
        pairs.append((client_rank, server_rank))
    return pairs

def xboard_mapping_mode(same_plane=False, all2all=False):
    if same_plane:
        return "all2all_same_plane" if all2all else "same_plane"
    return "all2all" if all2all else "many_to_one"

def validate_xboard_pairs(pairs, fields=None, ranks=None, link_mode="all", clients=None,
                          require_client_machine=False, require_ok=False, same_plane=False, all2all=False):
    if fields is None:
        fields = XBOARD_PAIR_REQUIRED_FIELDS
    if require_client_machine:
        selected = unique_ints(ranks)
        for pair in pairs:
            server_rank = int_value(pair.get("server_rank"))
            if (is_xboard_pair(pair) and pair_matches_link_mode(pair, link_mode)
                    and (not selected or server_rank in selected) and not client_machine(pair)):
                raise ValueError("remote_plan xboard pair missing client_machine")

    for pair in selected_xboard_pairs(pairs, ranks=ranks, link_mode=link_mode, clients=clients,
                                      same_plane=same_plane, all2all=all2all):
        if not pair.get("ok", True) and not require_ok:
            continue
        missing = missing_xboard_fields(pair, fields)
        if missing:
            if require_client_machine and "client_machine" in missing:
                raise ValueError("remote_plan xboard pair missing client_machine")
            raise ValueError(f"remote_plan xboard pair missing {','.join(missing)}")
        if require_ok and not pair.get("ok", True):
            reason = pair.get("reason") or pair.get("missing_fields") or "not executable"
            raise ValueError(f"remote_plan xboard pair not executable: {reason}")

def client_machine(pair):
    return str((pair or {}).get("client_machine") or "").strip().upper()

def ranks_from_pairs(pairs):
    return unique_ints(rank for pair in pairs for rank in pair_ranks(pair))

def pair_ranks(pair):
    if isinstance(pair, (list, tuple)):
        return [int_value(v) for v in pair]
    client = int_value(pair.get("client_rank"))
    server = int_value(pair.get("server_rank"))
    return [v for v in [client, server] if v is not None]

def command_result(target="", host="", cmd="", ok=True, rc=0, error_type="", stdout="", stderr="", elapsed=0.0,
                   backend="planner", port=0, **extra):
    result = {"target": target, "host": host, "port": port, "backend": backend, "cmd": cmd, "ok": bool(ok),
              "rc": int(rc), "error_type": error_type or "",
              "stdout": text_value(stdout), "stderr": text_value(stderr), "elapsed": round(float(elapsed), 3)}
    result.update(extra)
    return result

def tag_results(results, **fields):
    for result in results:
        result.update(fields)
    return results

def display_reason(value):
    text = str(value or "").strip()
    return "" if text.lower() in {"", "ready", "ping ok", "bandwidth ok", "done"} else text

def display_link(value):
    text = str(value or "").strip()
    return "" if text.lower() in {"", "no_topo_mapping"} else text

def has_link_distinction(items):
    return len({link for item in items or [] for link in [display_link((item or {}).get("link"))] if link}) > 1

def execution_host(pair, default="local"):
    if not isinstance(pair, dict):
        return default
    if is_xboard_pair(pair):
        return str(pair.get("client_cpu_host") or pair.get("host") or pair.get("client_machine") or default)
    return str(pair.get("execution_host") or default)

def role_eid(pair, role):
    return str((pair or {}).get(f"{role}_eid32") or "").strip()

def role_execute_eid(pair, role):
    return str((pair or {}).get(f"{role}_execute_eid") or "").strip()
