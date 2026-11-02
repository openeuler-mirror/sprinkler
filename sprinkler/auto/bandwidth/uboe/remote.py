"""Remote CPU OS access for uboe cross-board traffic.

The uboe parameter-plane ip (bond ip) lives on each card's device side and is only
reachable from that card. Cross-board (`-mode uboe-xboard -ip <peer cpu>`) means the peer
cpu's cards act as clients, so discovery / env pre-check / traffic must first go to the
peer CPU OS (paramiko) and then, inside that host, hop into each peer card's device
(`ssh root@<eid32>` via the peer's own ~/.ssh/config, BatchMode).

This mirrors the UBOE intra stack rule: device access goes through the eid32 + system ssh,
never the decoded IPv4 tail (paramiko to 223.0.x.x times out). Local cards keep using the
existing local `uboe/env.py::run_on_device`; only peer cards route through this module.
"""

import json
import time

from sprinkler.auto.bandwidth.common.route_conf import RouteConf
from sprinkler.auto.bandwidth.common.utils import int_value, DEFAULT_ROOTINFO
from sprinkler.config.host_device_pairs import parse_host_device_pairs_text
from sprinkler.connect.npu_access import npu_os_command, is_local_cpu_host

REMOTE_DEVICE_TIMEOUT = 15
REMOTE_READ_CHUNK = 65536


def remote_result(ok, rc, stdout, stderr, elapsed, host="", command=""):
    return {"ok": bool(ok), "rc": int(rc), "stdout": str(stdout or ""), "stderr": str(stderr or ""),
            "elapsed": round(float(elapsed), 3), "host": host, "cmd": command}


class RemoteDeviceExecutor:
    """Run commands on remote CPU OS hosts (one paramiko client per host, lazy)."""

    def __init__(self, connector, targets):
        self.connector = connector
        self.targets = {}
        for target in targets or []:
            if target is not None:
                self.targets[str(target.host or "")] = target
        self.clients = {}

    def run(self, cpu_host, command, timeout=REMOTE_DEVICE_TIMEOUT):
        """Run `command` on the remote cpu OS. Returns a remote_result dict."""
        host = str(cpu_host or "")
        client = self._client(host)
        if client is None:
            return remote_result(False, 255, "", f"no ssh target for cpu {host}", 0.0, host, command)
        start = time.time()
        try:
            _stdin, stdout, stderr = client.exec_command(str(command or ""), timeout=timeout)
            stdout.channel.settimeout(timeout)
            stderr.channel.settimeout(timeout)
            out = self._decode(stdout.read())
            err = self._decode(stderr.read())
            rc = int(stdout.channel.recv_exit_status())
            return remote_result(rc == 0, rc, out, err, time.time() - start, host, command)
        except Exception as exc:
            return remote_result(False, 255, "", str(exc), time.time() - start, host, command)

    def run_on_device(self, cpu_host, execute_eid, command, timeout=REMOTE_DEVICE_TIMEOUT):
        """From the remote cpu, hop into `execute_eid`'s device and run `command`."""
        if not execute_eid:
            return remote_result(False, 2, "", "connect addr missing", 0.0, cpu_host, command)
        nested = npu_os_command(execute_eid, command)
        return self.run(cpu_host, nested, timeout=timeout)

    def close(self):
        for client in self.clients.values():
            try:
                client.close()
            except Exception:
                pass
        self.clients.clear()

    def _client(self, cpu_host):
        host = str(cpu_host or "")
        if is_local_cpu_host(host):
            return None
        client = self.clients.get(host)
        if client is None:
            target = self.targets.get(host)
            if target is None:
                target = next((item for item in self.targets.values()
                               if str(item.host or "") == host), None)
            if target is None:
                return None
            client = self.connector.connect_client(target)
            self.clients[host] = client
        return client

    @staticmethod
    def _decode(value):
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")


def _load_rootinfo(rootinfo_text):
    """Normalize the remote rootinfo: a dict is used as-is, a string is json-parsed."""
    if isinstance(rootinfo_text, dict):
        return rootinfo_text
    try:
        return json.loads(str(rootinfo_text or "{}"))
    except (ValueError, TypeError):
        return {}


def remote_device_ids(rootinfo_text=None, fallback=range(8)):
    """Device ids present on the remote host: from its rootinfo rank_list, else 0..7."""
    data = _load_rootinfo(rootinfo_text)
    ids = [int(item.get("device_id")) for item in data.get("rank_list") or []
           if isinstance(item, dict) and int_value(item.get("device_id")) is not None]
    return sorted(ids) if ids else list(fallback)


def remote_device_ranks(rootinfo_text=None):
    """Map device id -> system rank on the remote host (same layout as host_device_pairs).

    Falls back to identity (device id == rank) when no rootinfo is readable.
    """
    data = _load_rootinfo(rootinfo_text)
    items = sorted((item for item in data.get("rank_list") or [] if isinstance(item, dict)),
                   key=lambda item: int_value(item.get("device_id")) if int_value(item.get("device_id")) is not None
                   else 10 ** 9)
    layout = []
    group = 0
    seen = set()
    previous = None
    for index, item in enumerate(items):
        device_id = int_value(item.get("device_id"), index)
        local_id = int_value(item.get("local_id"), index)
        if seen and (local_id in seen or previous is not None and local_id <= previous):
            group += 1
            seen = set()
        layout.append((device_id, group, local_id))
        seen.add(local_id)
        previous = local_id
    width = max((local_id for _device, _group, local_id in layout), default=-1) + 1
    return {device: group * max(width, 1) + local_id for device, group, local_id in layout}


def remote_device_pairs(executor, cpu_host, device_ids=None, rootinfo_text=None):
    """Read /proc/asdrv_ub (dev_id loop + pair_info) on the remote cpu.

    Returns {rank: [local_eid, remote_eid]} keyed in the same rank space as the local
    `host_device_pairs` (device_id -> rank via the remote rootinfo; device id used as rank
    when the mapping is unavailable).
    """
    device_ids = list(device_ids if device_ids is not None else remote_device_ids(rootinfo_text))
    ranks_map = remote_device_ranks(rootinfo_text)
    devices = " ".join(str(device) for device in device_ids)
    command = (f"for dev in {devices}; do "
               "echo $dev > /proc/asdrv_ub/dev_id 2>/dev/null || continue; "
               "echo __SPRINKLER_DEV__ $dev; "
               "cat /proc/asdrv_ub/pair_info 2>/dev/null || true; done; true")
    result = executor.run(cpu_host, command)
    if not result.get("ok"):
        return {}
    raw = parse_host_device_pairs_text(result.get("stdout"))
    pairs = {}
    for device_id, values in raw.items():
        rank = ranks_map.get(device_id, device_id)
        pairs[rank] = values
    return pairs


def remote_rootinfo(executor, cpu_host, path=DEFAULT_ROOTINFO):
    result = executor.run(cpu_host, f"cat {path} 2>/dev/null || true")
    return result.get("stdout") or "" if result.get("ok") else ""


def format_remote_eid(value):
    return RouteConf.format_eid32(value) if value else ""
