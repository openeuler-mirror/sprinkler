"""Resolve URMA EID data from urma_admin show output."""

import re

from sprinkler.auto.bandwidth.common.route_conf import RouteConf
from sprinkler.auto.bandwidth.common import is_xboard_pair, role_eid, role_execute_eid
from sprinkler.connect.npu_access import is_local_cpu_host


class UrmaShowParser:
    UDMA_RE = re.compile(r"\b(udma\w+)\b", re.IGNORECASE)
    EID_RE = re.compile(r"\b(eid\d+)\b", re.IGNORECASE)
    EID_IDX_RE = re.compile(r"\beid(?:_|\s*)idx\s*[:=]?\s*(\d+)\b", re.IGNORECASE)
    ADDR_RE = re.compile(r"\b([0-9a-fA-F]{4}(?::[0-9a-fA-F]{4}){7})\b")
    ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
    CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

    def parse(self, text):
        result = {}
        clean_text = self.CTRL_RE.sub("", self.ANSI_RE.sub("", str(text or "").replace("\r", "")))
        for raw in clean_text.splitlines():
            line = raw.strip()
            header = line.lower()
            if not line or header.startswith("num") or header.startswith("---") or header.startswith("index"):
                continue
            udma = self.UDMA_RE.search(line)
            addr = self.ADDR_RE.search(line)
            if not (udma and addr):
                continue
            suffix = RouteConf.suffix4(addr.group(1))
            if suffix:
                explicit = self.EID_IDX_RE.search(line)
                eid = self.EID_RE.search(line)
                eid_idx = explicit.group(1) if explicit else "0"
                if not explicit and eid:
                    eid_idx = re.sub(r"[^0-9]", "", eid.group(1)) or "0"
                eid32 = RouteConf.format_eid32(addr.group(1))
                info = {"udma": udma.group(1).lower(), "eid_idx": eid_idx, "eid32": eid32, "line": line}
                result[eid32] = info
                result[RouteConf.clean_hex(eid32)] = info
                result[suffix] = info
        return result


class UrmaShowResolver:
    SHOW_COMMAND = "urma_admin show"
    SHOW_TIMEOUT = 20

    def __init__(self, executor, parser=None):
        self.executor = executor
        self.parser = parser or UrmaShowParser()
        self.cache = {}
        self.failures = {}

    def fill(self, pairs):
        resolved = []
        for item in pairs:
            pair = dict(item)
            if not pair.get("ok", True):
                resolved.append(pair)
                continue
            for role in ("client", "server"):
                side = self.side(pair, role)
                show_map = self.show_map(side)
                eid32 = RouteConf.format_eid32(role_eid(pair, role))
                suffix = RouteConf.suffix4(eid32)
                info = show_map.get(eid32) or show_map.get(RouteConf.clean_hex(eid32)) or show_map.get(suffix)
                if not info:
                    key = side.get("label")
                    reason = self.failures.get(key) or f"{role} eid not found in urma show: {suffix or 'NA'}"
                    pair.update(ok=False, rc=2, error_type="urma_show", reason=reason)
                    continue
                pair[f"{role}_udma"] = info["udma"]
                pair[f"{role}_eid_idx"] = info["eid_idx"]
            resolved.append(pair)
        return resolved

    def show_map(self, side):
        key = side.get("label")
        if key in self.cache:
            return self.cache[key]
        result = self.executor.run(side, self.SHOW_COMMAND, timeout=self.SHOW_TIMEOUT)
        if not result.get("ok"):
            self.cache[key] = {}
            text = result.get("stderr") or result.get("stdout") or result.get("reason") or result.get("error_type")
            self.failures[key] = f"{side.get('label')} urma show failed: {text or 'unknown'}"
            return self.cache[key]
        self.cache[key] = self.parser.parse(result.get("stdout"))
        return self.cache[key]

    def side(self, pair, role):
        target = str(pair.get(f"{role}_target") or "")
        rank = pair.get(f"{role}_rank")
        if target.startswith("CPU"):
            return {"kind": "local", "label": target, "host": "local", "rank": rank}
        cpu_host = str(pair.get(f"{role}_cpu_host") or "").strip()
        if is_xboard_pair(pair) and cpu_host:
            execute_eid = role_execute_eid(pair, role)
            kind = "local" if is_local_cpu_host(cpu_host) else "ssh"
            host = "local" if kind == "local" else cpu_host
            error = "" if execute_eid else f"{target} execute_eid missing"
            return {"kind": kind, "label": target, "host": host, "rank": rank,
                    "execute_eid": execute_eid, "error": error}
        host = role_execute_eid(pair, role)
        error = "" if host else f"{target} execute_eid missing"
        return {"kind": "local", "label": target, "host": host, "rank": rank,
                "execute_eid": host, "error": error}
