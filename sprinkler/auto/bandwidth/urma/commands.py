"""Build URMA listener/client commands from EID pairs."""

import shlex

from sprinkler.auto.bandwidth.planning.eid_pairs import plan_eid_pairs
from sprinkler.auto.bandwidth.common import command_result, pair_context, pair_side_text, is_xboard_pair, missing_xboard_fields, role_eid, args_link_mode, is_latency_mode, args_mode_list
from sprinkler.auto.bandwidth.urma.executor import UrmaCommandExecutor
from sprinkler.auto.bandwidth.urma.show_resolver import UrmaShowResolver
from sprinkler.utils.utils import positive_int_or_default

URMA_DEFAULT_OPTS = ("-R 4096 -T 4096 -C 16384 -p 0 -y -s 16777216 -L -l 256 -Q 256 "
                     "--tp_aware --ctp -B --hugepage_size 2MB --enable_ipv6")
URMA_LATENCY_OPTS = "-n 10 -s 8 -I 128 --tp_aware --priority 0 --enable_ipv6"

def default_urma_opts(mode, link_mode):
    opts = URMA_LATENCY_OPTS if is_latency_mode(mode) else URMA_DEFAULT_OPTS
    if link_mode == "ubg":
        opts = " ".join(token for token in shlex.split(opts) if token not in {"--ctp", "-ctp"})
    return opts

class UrmaPortPlanner:
    PORT_MIN = 1
    PORT_MAX = 65535
    KIND_OFFSETS = {"h2d": 0, "d2h": 64, "d2d": 128, "xboard": 256}

    def __init__(self, port_base, port_stride):
        self.port_base = int(port_base)
        self.port_stride = int(port_stride)

    def port(self, pair, index):
        kind = self.kind(pair)
        return self.port_base + self.KIND_OFFSETS[kind] + int(index)

    def port_in_range(self, port):
        return self.PORT_MIN <= int(port) <= self.PORT_MAX

    def kind(self, pair):
        text = str(pair.get("kind") or "").lower()
        scope = str(pair.get("scope") or "").lower()
        if "xboard" in text or scope == "xboard":
            return "xboard"
        if "d2h" in text:
            return "d2h"
        if "d2d" in text:
            return "d2d"
        return "h2d"

class UrmaCommandBuilder:
    REQUIRED_FIELDS = ("client_udma", "server_udma", "client_eid_idx", "server_eid_idx")

    def __init__(self, args=None):
        self.args = args
        self.link_mode = str(getattr(args, "mode", "") or "").strip()
        self.mode = str(getattr(args, "urma_mode", "") or "write_bw").strip() or "write_bw"
        self.opts = str(getattr(args, "urma_opts", "") or "").strip() or default_urma_opts(self.mode, self.link_mode)
        self.single = bool(getattr(args, "single", False))
        self.port_base = positive_int_or_default(getattr(args, "port_base", 52000), 52000, "-port-base")
        self.port_stride = positive_int_or_default(getattr(args, "port_stride", 256), 256, "-port-stride")
        self.duration = positive_int_or_default(getattr(args, "duration", 30), 30, "-duration")
        self.port_planner = UrmaPortPlanner(self.port_base, self.port_stride)

    def listener(self, pair, port):
        cmd = (f"urma_perftest {self.mode} "
               f"-d {pair.get('server_udma')} "
               f"--port {int(port)} "
               f"--eid_idx {pair.get('server_eid_idx')} "
               f"{self.opts_for_pair(pair)}").strip()
        return self.command_item("listener", pair.get("server_target", ""), pair, port, cmd)

    def client(self, pair, port):
        cmd = (f"urma_perftest {self.mode} "
               f"-d {pair.get('client_udma')} "
               f"-S {role_eid(pair, 'server')} "
               f"--port {int(port)} "
               f"--eid_idx {pair.get('client_eid_idx')} "
               f"--bind_ip {role_eid(pair, 'client')} "
               f"{self.opts_for_pair(pair)}").strip()
        return self.command_item("client", pair.get("client_target", ""), pair, port, cmd)

    def plan_commands(self, pairs):
        return [item for index, pair in enumerate(pairs) for item in self.command_items(pair, index)]

    def command_items(self, pair, index):
        reason, port = self.not_ready_reason(pair, index)
        if reason:
            return [self.not_executable(pair, reason, port)]
        return [self.listener(pair, port), self.client(pair, port)]

    def not_ready_reason(self, pair, index):
        if not pair.get("ok", True):
            return pair.get("reason") or "bad pair", ""
        missing = missing_xboard_fields(pair) or self.missing_command_fields(pair)
        if missing:
            return f"not executable: missing {','.join(missing)}", ""
        port = self.port_planner.port(pair, index)
        if not self.port_planner.port_in_range(port):
            return f"port out of range: {port}", port
        return "", port

    def command_item(self, role, target, pair, port, cmd):
        has_udma = pair.get("client_udma") and pair.get("server_udma")
        has_eid_idx = pair.get("client_eid_idx") and pair.get("server_eid_idx")
        ready = bool(has_udma and has_eid_idx)
        reason = "ready" if ready else "udma/eid_idx not resolved"
        host_by_role = {"listener": role_eid(pair, "server"), "client": role_eid(pair, "client")}
        host = host_by_role.get(role, "")
        return command_result(target=f"{role}:{target}", host=host, cmd=cmd, ok=bool(pair.get("ok")),
                              rc=0 if pair.get("ok") else 2,
                              error_type="" if pair.get("ok") else "config", role=role,
                              command_target=target, **self.command_extra(pair, int(port), ready, reason))

    def not_executable(self, pair, reason, port=""):
        target = f"not_executable:{pair.get('client_target', '')}->{pair.get('server_target', '')}"
        return command_result(target=target, ok=False, rc=2, error_type="config", stderr=reason,
                              role="not_executable", command_target=pair.get("target", ""),
                              **self.command_extra(pair, port, False, reason))

    def missing_command_fields(self, pair):
        missing = [field for field in self.REQUIRED_FIELDS if pair.get(field) in ("", None)]
        for role in ("client", "server"):
            field = f"{role}_execute_eid" if pair.get("kind") == "intra_d2d" else f"{role}_eid32"
            if pair.get(field) in ("", None):
                missing.append(field)
        return missing

    def command_extra(self, pair, port, ready, reason):
        extra = {**pair_context(pair), "urma_port": port, "urma_mode": self.mode, "duration": self.duration,
                 "ready": ready, "reason": reason}
        if is_xboard_pair(pair):
            extra["link_mode"] = args_link_mode(self.args)
        return extra

    def opts_for_pair(self, pair):
        tokens = shlex.split(self.opts)
        if self.single:
            tokens = [token for token in tokens if token != "-B"]
        return " ".join(shlex.quote(token) for token in tokens)
