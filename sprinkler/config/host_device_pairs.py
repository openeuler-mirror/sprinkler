import json
import os
import re
import subprocess
from pathlib import Path

from sprinkler.arguments import get_args, parse_args
from sprinkler.auto.bandwidth.common.route_conf import RouteConf
from sprinkler.auto.bandwidth.common import DEFAULT_ROOTINFO
from sprinkler.base.log import RunLog

EID_TEXT_RE = re.compile(r"(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}|(?:0x)?[0-9a-fA-F]{16,}")
_HOST_DEVICE_PAIRS_ = {}

STATIC_NPU_IPS = {
    0: "192.168.2.199",
    1: "192.168.2.198",
    2: "192.168.2.197",
    3: "192.168.2.196",
}

def parse_pair_info(text):
    values = {"local_eid": "", "remote_eid": ""}
    for raw in str(text or "").splitlines():
        line = raw.strip()
        lower = line.lower()
        eids = EID_TEXT_RE.findall(line)
        if "local_eid" in lower and eids:
            values["local_eid"] = RouteConf.clean_hex(eids[-1])
        elif "remote_eid" in lower and eids:
            values["remote_eid"] = RouteConf.clean_hex(eids[-1])
    local_eid, remote_eid = values["local_eid"], values["remote_eid"]
    return [local_eid, remote_eid] if local_eid and remote_eid else []

def parse_host_device_pairs_text(text):
    pairs = {}
    current = None
    lines = []

    def flush():
        if current is None:
            return
        values = parse_pair_info("\n".join(lines))
        if values:
            pairs[current] = values

    for raw in str(text or "").splitlines():
        line = raw.strip()
        if line.startswith("__SPRINKLER_DEV__"):
            flush()
            value = line.rsplit(" ", 1)[-1]
            current = int(value) if value.isdecimal() else None
            lines = []
        else:
            lines.append(raw)
    flush()
    return pairs

def rootinfo_device_ranks(path=DEFAULT_ROOTINFO):
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8", errors="ignore"))
    except (OSError, ValueError):
        return {device: device for device in range(8)}
    items = sorted((item for item in data.get("rank_list") or [] if isinstance(item, dict)),
                   key=lambda item: int(item.get("device_id", 10**9)))
    layout = []
    group = 0
    seen = set()
    previous = None
    for index, item in enumerate(items):
        device_id = int(item.get("device_id", index))
        local_id = int(item.get("local_id", index))
        if seen and (local_id in seen or previous is not None and local_id <= previous):
            group += 1
            seen = set()
        layout.append((device_id, group, local_id))
        seen.add(local_id)
        previous = local_id
    width = max((local_id for _device, _group, local_id in layout), default=-1) + 1
    return {device: group * max(width, 1) + local_id for device, group, local_id in layout}

def _get_static_host_and_device_pairs():
    global _HOST_DEVICE_PAIRS_
    try:
        local_ip = subprocess.check_output(
            "hostname -I | awk '{print $1}'", shell=True, text=True
        ).strip().split()[0]
    except Exception:
        local_ip = "127.0.0.1"
    for rank, npu_ip in STATIC_NPU_IPS.items():
        _HOST_DEVICE_PAIRS_[rank] = [local_ip, npu_ip]
    return _HOST_DEVICE_PAIRS_

def _get_host_and_device_pairs():
    base = "/proc/asdrv_ub"
    dev_id_path = os.path.join(base, "dev_id")
    pair_info_path = os.path.join(base, "pair_info")
    
    if not os.path.exists(base):
        return _get_static_host_and_device_pairs()
    
    for path, kind in ((dev_id_path, "file"), (pair_info_path, "file")):
        if not os.path.exists(path):
            return _get_static_host_and_device_pairs()

    global _HOST_DEVICE_PAIRS_
    for dev_id, rank in rootinfo_device_ranks().items():
        try:
            subprocess.run(["sh", "-c", f"echo {dev_id} > {dev_id_path}"], check=True)
            with open(pair_info_path, encoding="utf-8", errors="ignore") as handle:
                values = parse_pair_info(handle.read())
            if values:
                _HOST_DEVICE_PAIRS_[rank] = values
            else:
                print(f"dev_id {dev_id}: host/device pair_info missing")
        except subprocess.CalledProcessError:
            print(f"set dev_id {dev_id} failed, root permission may be required")
        except Exception as exc:
            print(f"process dev_id {dev_id} failed: {exc}")

def get_host_and_device_pairs():
    if not _HOST_DEVICE_PAIRS_:
        _get_host_and_device_pairs()
    return _HOST_DEVICE_PAIRS_

def print_host_and_device_pairs(args=None, mode="h2d"):
    args = args or get_args()
    run_log = RunLog(args, default_check_id="eid_pairs")
    pairs = get_host_and_device_pairs()
    run_log.info(f"mode={mode} pair_total={len(pairs)}")
    for rank, pair in pairs.items():
        print(pair)
        kind = "h2d" if mode == "h2d" else "d2h"
        client, server = (pair[0], pair[1]) if mode == "h2d" else (pair[1], pair[0])
        run_log.info(f"pair={rank:03d} kind=intra_{kind} scope=intra runner={mode} "
                     f"client={client} server={server} link=topo")

if __name__ == "__main__":
    print_host_and_device_pairs(parse_args(None))
