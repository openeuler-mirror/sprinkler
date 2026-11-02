"""UBOE target discovery: auto map npu rank -> {execute_eid, npu_ip, bond_ip}.

The uboe ip (parameter-plane bond ip) is only reachable between devices, not from the
host. To enter each card's device side we reuse the SAME login address the URMA intra/d2d
flow uses (proven via `ping_check`/`urma_show`): the card's `execute_eid`, which is the
full 128-bit eid32 string of `remote_eid`, resolved to the device through ~/.ssh/config
(system `ssh -tt -o BatchMode=yes root@<eid32>`).

(!) Do NOT ssh to the decoded IPv4 tail (223.0.x.0) via paramiko - that path times out on
    every card; only the eid32 + system-ssh pipe reaches the device in this stack.

Then, inside the device, the ub/oe bond ip is read (ethc0d1e3 or its master bond).
Both addresses per card:
  - execute_eid = eid32 (login into the device to run ifconfig / urma_perftest);
  - bond_ip     = uboe ip (only reachable between devices; fills --sip/--dip/-S).
"""

import re

from sprinkler.auto.bandwidth.common import parse_bond_ip_map, RouteConf
from sprinkler.auto.bandwidth.uboe.env import run_on_device

# Parameter-plane slave NIC for the uboe bond. If it has no own addr it is a member of a
# bond whose master carries the uboe ip.
UBOE_SLAVE_IFACE = "ethc0d1e3"

# Read the ub/oe bond ip inside a device: own addr on the slave, else its bond master.
BOND_IP_COMMAND = (
    "ip=$(ip -o -4 addr show dev ethc0d1e3 2>/dev/null | awk '{print $4}'); "
    "if [ -z \"$ip\" ]; then "
    "B=$(basename $(readlink /sys/class/net/ethc0d1e3/master 2>/dev/null) 2>/dev/null); "
    "if [ -n \"$B\" ]; then ip=$(ip -o -4 addr show dev \"$B\" 2>/dev/null | awk '{print $4}'); fi; "
    "fi; "
    "echo \"$ip\""
)


def eid_normalize(eid):
    text = str(eid or "").replace(":", "").strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    return text


def eid_to_ipv4(eid):
    """Tail 8 hex chars of the eid encode the device IPv4 (e.g. ...dfdf0890 -> 223.223.8.144)."""
    text = eid_normalize(eid)
    last8 = text[-8:]
    if len(last8) != 8 or not re.fullmatch(r"[0-9a-f]{8}", last8):
        return ""
    try:
        return "%d.%d.%d.%d" % tuple(int(last8[i:i + 2], 16) for i in (0, 2, 4, 6))
    except ValueError:
        return ""


def execute_eid_of(remote_eid):
    """The device login address used for ssh: the card's eid32 (matches d2d execute_eid)."""
    return RouteConf.format_eid32(remote_eid)


def discover_bond_ip(login):
    """Enter the device (login = eid32) and read the ub/oe bond ip."""
    if not login:
        return ""
    result = run_on_device(login, BOND_IP_COMMAND)
    if not result.get("ok"):
        return ""
    match = re.search(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", result.get("stdout") or "")
    return match.group(1) if match else ""


def _get_host_device_pairs():
    try:
        from sprinkler.config.host_device_pairs import get_host_and_device_pairs
        return get_host_and_device_pairs() or {}
    except Exception:
        return {}


def collect_uboe_targets(args):
    """Return {rank: {"execute_eid": str, "npu_ip": str, "bond_ip": str}}.

    `execute_eid` is the device login address (eid32 over system ssh); `npu_ip` is the
    decoded IPv4 kept for reporting; `bond_ip` is the uboe ip (for --sip/--dip/-S). A
    `-bond-ip` map overrides the discovered uboe ip.
    """
    manual = parse_bond_ip_map(getattr(args, "bond_ip_map", "") or "")
    pairs_map = _get_host_device_pairs()
    targets = {}
    for rank, values in sorted(pairs_map.items()):
        remote = values[1] if isinstance(values, (list, tuple)) and len(values) > 1 else values
        login = execute_eid_of(remote)
        if not login:
            continue
        npu_ip = eid_to_ipv4(remote)
        bond_ip = manual.get(int(rank)) or discover_bond_ip(login)
        if bond_ip:
            targets[int(rank)] = {"execute_eid": login, "npu_ip": npu_ip, "bond_ip": bond_ip}
    return targets


def discover_bond_ip_remote(remote_executor, cpu_host, login):
    """From the peer cpu, enter the peer card's device and read its ub/oe bond ip."""
    if not login:
        return ""
    result = remote_executor.run_on_device(cpu_host, login, BOND_IP_COMMAND)
    if not result.get("ok"):
        return ""
    match = re.search(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", result.get("stdout") or "")
    return match.group(1) if match else ""


def collect_remote_uboe_targets(remote_executor, cpu_host, bond_ip_map=None):
    """Return {rank: {"execute_eid": str, "npu_ip": str, "bond_ip": str}} for ONE peer cpu.

    Discovery runs on the peer host: read its /proc/asdrv_ub (rootinfo ranks), then for
    each card enter the device (eid32 over the peer's system ssh) and read the bond ip.
    A card whose device hop or bond ip fails is skipped (rank not in the result). A
    `-bond-ip` map still overrides the discovered peer uboe ip.
    """
    from sprinkler.auto.bandwidth.uboe.remote import (
        remote_device_pairs, remote_rootinfo, format_remote_eid)
    manual = parse_bond_ip_map(bond_ip_map or "")
    rootinfo_text = remote_rootinfo(remote_executor, cpu_host)
    pairs_map = remote_device_pairs(remote_executor, cpu_host, rootinfo_text=rootinfo_text)
    from sprinkler.auto.bandwidth.uboe.remote import remote_device_ids
    if not pairs_map:
        for device_id in remote_device_ids(rootinfo_text):
            pairs_map[device_id] = []
    targets = {}
    for rank, values in sorted(pairs_map.items()):
        remote = values[1] if isinstance(values, (list, tuple)) and len(values) > 1 else values
        login = execute_eid_of(remote) or format_remote_eid(remote)
        if not login:
            continue
        npu_ip = eid_to_ipv4(remote)
        bond_ip = manual.get(int(rank)) or discover_bond_ip_remote(remote_executor, cpu_host, login)
        if bond_ip:
            targets[int(rank)] = {"execute_eid": login, "npu_ip": npu_ip, "bond_ip": bond_ip}
    return targets
