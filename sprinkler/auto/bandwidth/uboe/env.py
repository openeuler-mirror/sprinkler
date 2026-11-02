"""UBOE environment / readiness checks, run on each card's device side.

Mirrors the pre-check role of `planning/ping_check.py` but executes on the device side
(ssh into each card via its execute_eid), because `ifconfig` (ethc0d1e3/e4 NIC) and the
bond/uboe ip are only accurate there.

Per card we report precisely the two UBOE-relevant facts: the matched parameter-plane NIC
name(s) (`ethc0d1e3`/`ethc0d1e4`) and the card's uboe bond ip. On the traffic pre-check we
additionally show, per pair, the `ping` command run from the client device to the server
bond ip. Every involved npu is checked exactly once (deduplicated), not once per pair.

Two consumers:
  1. `-uboe-env-check` (independent command): per-npu NIC + bond ip report (device-side).
  2. `-urma-bandwidth -mode uboe` pre-check: `precheck_pairs_for_traffic` gates traffic.
"""

import re
import shlex
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from sprinkler.auto.bandwidth.common import command_result, pair_context, int_value, role_execute_eid
from sprinkler.auto.bandwidth.common.pairs import execution_host
from sprinkler.base.log import RunLog
from sprinkler.base.results import CommandResultView
from sprinkler.connect.npu_access import npu_os_command, is_local_cpu_host

CHECK_ID = "uboe_env"
DEVICE_TIMEOUT = 10
PING_TIMEOUT = 10
PING_OPTIONS = "-c 3 -W 2 -w 6"
ENV_WORKERS = 8


def run_local_command(command, timeout):
    start = time.time()
    try:
        completed = subprocess.run(command, shell=True, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=timeout)
        return {"ok": completed.returncode == 0, "rc": completed.returncode,
                "stdout": completed.stdout or "", "stderr": completed.stderr or "",
                "elapsed": time.time() - start}
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "rc": 255, "stdout": exc.stdout or "", "stderr": exc.stderr or str(exc),
                "elapsed": time.time() - start}


def run_on_device(connect_addr, command, timeout=DEVICE_TIMEOUT):
    """Run on a card's device side by ssh-ing from the host via the system ssh.

    `npu_os_command` builds `ssh -tt -o BatchMode=yes ... root@<execute_eid> <cmd>` which
    resolves to the device through ~/.ssh/config (key-based, BatchMode). `connect_addr` is
    the device login address (`execute_eid` = the card eid32), NOT the uboe ip.
    """
    host = str(connect_addr or "").strip()
    if not host:
        return {"ok": False, "rc": 2, "stdout": "", "stderr": "connect addr missing", "elapsed": 0.0}
    raw = npu_os_command(host, str(command or ""))
    return run_local_command(raw, timeout)


def shlex_quote(value):
    return shlex.quote(str(value or ""))


def card_info(rank, connect_addr, bond_ip, cpu_host="", remote_executor=None):
    """One device-side "card info": matched parameter-plane NIC(s) + the uboe bond ip.

    Peers host cards when `cpu_host` is a non-local host: then the checks are driven from
    the peer cpu OS (nested device ssh), otherwise from the local host as today.
    """
    command = "ifconfig 2>/dev/null | grep -E 'ethc0d1e[34]' -o | sort -u"
    if remote_executor is not None and cpu_host and not is_local_cpu_host(cpu_host):
        result = remote_executor.run_on_device(cpu_host, connect_addr, command)
    else:
        result = run_on_device(connect_addr, command)
    ifaces = " ".join(str(result["stdout"] or "").split())
    ok = bool(ifaces) and bool(bond_ip)
    reason = "" if ok else ("no e3/e4" if not ifaces else "bond ip missing")
    return command_result(target=f"npu{rank}", host=connect_addr or cpu_host or "local", cmd=command,
                          ok=ok, rc=result["rc"], error_type="" if ok else "uboe_env",
                          stdout=result["stdout"], stderr=result["stderr"], elapsed=result["elapsed"],
                          rank=rank, ifaces=ifaces, bond_ip=bond_ip or "", reason=reason)


def card_info_text(result):
    parts = [str(result.get("target") or "npu")]
    if result.get("ifaces"):
        parts.append(f"iface={result['ifaces']}")
    if result.get("bond_ip"):
        parts.append(f"bond_ip={result['bond_ip']}")
    if not result.get("ok") and result.get("reason"):
        parts.append(f"reason={result['reason']}")
    return " ".join(parts)


def connectivity_check(client_execute_eid, server_bond_ip, client_cpu_host="", remote_executor=None):
    """Client device pings server ub/oe bond-ip; 0% loss == link up.
    Returns (ok, reason, loss, cmd). Peer-client flows ping from the peer cpu's device."""
    cmd = f"ping {PING_OPTIONS} {shlex_quote(str(server_bond_ip))}"
    if not client_execute_eid or not server_bond_ip:
        return False, "connect addr / bond ip missing", None, cmd
    if remote_executor is not None and client_cpu_host and not is_local_cpu_host(client_cpu_host):
        res = remote_executor.run_on_device(str(client_cpu_host), str(client_execute_eid),
                                            cmd, timeout=PING_TIMEOUT)
    else:
        res = run_on_device(str(client_execute_eid), cmd, timeout=PING_TIMEOUT)
    if not res["ok"]:
        err = (str(res.get("stderr") or "").strip() or str(res.get("stdout") or "").strip()
               or "device command failed")
        return False, f"device exec failed: {err[:160]}", None, cmd
    loss = None
    match = re.search(r"(\d+)% packet loss", res["stdout"])
    if match:
        loss = int(match.group(1))
    ok = loss == 0
    return ok, ("link up" if ok else f"link loss={loss}%"), loss, cmd


def card_key(rank, cpu_host):
    """One physical card = (its cpu host, rank). Local cards: rank+local marker. Cross-board
    peers and local cards share the same rank space, so the host must be in the key to avoid
    one machine's card overwriting the other's."""
    return (str(cpu_host or "").strip() or "local", int_value(rank))


def _involved_ranks(pairs):
    info = {}
    for pair in pairs:
        for role in ("client", "server"):
            rank = int_value(pair.get(f"{role}_rank"))
            if rank is None:
                continue
            cpu_host = str(pair.get(f"{role}_cpu_host") or "").strip()
            info[card_key(rank, cpu_host)] = {
                "rank": rank,
                "connect_addr": str(role_execute_eid(pair, role) or "").strip(),
                "bond_ip": str(pair.get(f"{role}_bond_ip") or "").strip(),
                "cpu_host": cpu_host,
            }
    return info


def precheck_pairs_for_traffic(args, pairs, run_log=None, remote_executor=None):
    """Gate traffic pairs. Every involved npu is card-checked once (NIC + bond ip), then
    each pair's client device pings the server bond ip. Returns (ready_pairs, failed_results).
    `remote_executor` enables peer-host cards (cross-board uboe-xboard)."""
    info = _involved_ranks(pairs)
    keys = sorted(info)
    if run_log is not None:
        run_log.info(f"uboe_env_check=started npu_total={len(keys)}")

    card_ok = {}
    items = list(enumerate(keys))
    results = [None] * len(items)

    def check(job):
        i, key = job
        entry = info[key]
        return i, key, card_info(entry["rank"], entry["connect_addr"], entry["bond_ip"],
                                 entry["cpu_host"], remote_executor)

    with ThreadPoolExecutor(max_workers=min(ENV_WORKERS, max(len(items), 1))) as pool:
        for i, key, result in pool.map(check, items):
            results[i] = result
            card_ok[key] = bool(result.get("ok"))
            if run_log is not None:
                run_log.log("PASS" if result.get("ok") else "WARN", card_info_text(result))

    ready, failed = [], []
    for pair in pairs:
        reasons = []
        pair_ok = True
        for role in ("client", "server"):
            rank = int_value(pair.get(f"{role}_rank"))
            key = card_key(rank, pair.get(f"{role}_cpu_host") or "")
            if rank is not None and not card_ok.get(key):
                pair_ok = False
                reasons.append(f"{role}=card not ready")
        client_exec = role_execute_eid(pair, "client")
        server_bond = str(pair.get("server_bond_ip") or "").strip()
        client_cpu_host = str(pair.get("client_cpu_host") or "").strip()
        if client_exec and server_bond:
            ok, reason, _loss, cmd = connectivity_check(client_exec, server_bond,
                                                        client_cpu_host, remote_executor)
            if run_log is not None:
                target = f"{pair.get('client_target')}->{pair.get('server_target')}"
                run_log.log("PASS" if ok else "FAIL", f"ping {target} cmd={cmd}")
            if not ok:
                pair_ok = False
                reasons.append(reason)
        if pair_ok:
            ready.append(pair)
        else:
            failed.append(env_failed_result(pair, ";".join(reasons)))

    if run_log is not None:
        run_log.info(f"uboe_env_check=finished ready={len(ready)} env_failed={len(failed)}")
    return ready, failed


def env_failed_result(pair, reason):
    return command_result(target=pair.get("target") or pair_target(pair), host=execution_host(pair),
                          cmd="uboe env check", ok=False, rc=2, error_type="env",
                          stderr=reason,
                          **{**pair_context(pair), "bandwidth_phase": "uboe",
                             "execution_status": "env_failed", "ping": False, "reason": reason})


def pair_target(pair):
    return f"uboe:{pair.get('client_target')}->{pair.get('server_target')}"


def run_uboe_env(args):
    from sprinkler.auto.bandwidth.uboe.discovery import collect_uboe_targets
    targets = collect_uboe_targets(args)
    if not targets:
        raise ValueError("-uboe-env-check: no UBOE targets discovered "
                         "(-bond-ip can override the uboe ip)")

    ranks = sorted(targets)
    rank_spec = str(getattr(args, "rank", "") or "").strip()
    if rank_spec:
        from sprinkler.base.ranges import parse_range
        wanted = set(parse_range(rank_spec))
        ranks = [rank for rank in ranks if rank in wanted]
    if not ranks:
        raise ValueError("-uboe-env-check: no UBOE targets for the requested rank "
                         f"(available: {sorted(targets)})")

    def run(rank):
        target = targets[rank]
        return card_info(rank, target.get("execute_eid"), target.get("bond_ip"))

    results = [None] * len(ranks)
    with ThreadPoolExecutor(max_workers=min(ENV_WORKERS, len(ranks))) as pool:
        for index, result in enumerate(pool.map(run, ranks)):
            results[index] = result
    return results


class UboeEnvResultView:
    """ping_check-style log view for a card-info result: npuR iface=... bond_ip=..."""

    def __init__(self, formatter):
        self.formatter = formatter
        self.command_view = CommandResultView(formatter)

    def summary(self, result: dict, level: str):
        return card_info_text(result)

    def output_blocks(self, result: dict, ok: bool, show_output: bool):
        item = result if result.get("target") else {**result, "target": CHECK_ID}
        blocks = self.command_view.output_blocks(item, ok, show_output)
        return [(level, prefix, text, False) for level, prefix, text, _show in blocks
                if "[detail]" not in prefix]


def uboe_env_execute(args):
    run_log = RunLog(args, default_check_id=CHECK_ID)
    run_log.info(f"log_dir={run_log.path}")
    try:
        results = run_uboe_env(args)
    except Exception as exc:
        return run_log.fail_error(CHECK_ID, exc)
    return run_log.finish_results(CHECK_ID, results, UboeEnvResultView(run_log.formatter))
