"""Build UBOE urma_perftest listener/client commands from planned pairs.

Mirrors `urma/commands.py`. The UBOE golden command shape (user-confirmed):

    # listener / server (no -S)
    urma_perftest write_bw -d <server_udma> -P <port> --uboe \
        <uboe-opts> --sip <server_bond_ip> --dip <client_bond_ip>
    # client
    urma_perftest write_bw -d <client_udma> -S <server_bond_ip> -P <port> --uboe \
        <uboe-opts> --sip <client_bond_ip> --dip <server_bond_ip>

Semantics:
  -S     peer (listener) bond ip  (client only)
  -P     perftest port
  --sip  local card bond ip
  --dip  remote data target bond ip
  -d     local card udma channel (udmac0d1e3, the parameter-plane bond)
"""

import shlex

from sprinkler.auto.bandwidth.common import command_result, pair_context, is_latency_mode
from sprinkler.utils.utils import positive_int_or_default

UBOE_PORT_MIN, UBOE_PORT_MAX = 1, 65535
# Wiki UBOE example uses 62600; no CLI flag exists, so UBOE pins its own default.
UBOE_PORT_DEFAULT = 62600
UBOE_REQUIRED_FIELDS = ("client_udma", "server_udma", "client_bond_ip", "server_bond_ip")
UBOE_DEFAULT_OPTS = "-s 67108864 --tp_aware --hugepage_size 2MB --dscp 33 -y"
UBOE_LATENCY_OPTS = "-n 10 -s 8 -I 128 --tp_aware --priority 0"

def default_uboe_opts(mode):
    return UBOE_LATENCY_OPTS if is_latency_mode(mode) else UBOE_DEFAULT_OPTS


class UboePortPlanner:
    """UBOE ports are a flat base+index range (no URMA kind offset needed)."""

    def __init__(self, port_base):
        self.port_base = int(port_base)

    def port(self, pair, index):
        return self.port_base + int(index)

    def port_in_range(self, port):
        return UBOE_PORT_MIN <= int(port) <= UBOE_PORT_MAX


class UboeCommandBuilder:
    """Build UBOE urma_perftest listener/client commands from planned pairs."""

    def __init__(self, args=None):
        self.args = args
        self.mode = str(getattr(args, "urma_mode", "") or "").strip() or "write_bw"
        self.opts = str(getattr(args, "uboe_opts", "") or "").strip() or default_uboe_opts(self.mode)
        self.single = bool(getattr(args, "single", False))
        self.port_planner = UboePortPlanner(
            positive_int_or_default(getattr(args, "uboe_port", UBOE_PORT_DEFAULT), UBOE_PORT_DEFAULT, "-uboe-port"))
        self.duration = positive_int_or_default(getattr(args, "duration", 30), 30, "-duration")

    def not_ready_reason(self, pair, index):
        if not pair.get("ok", True):
            return pair.get("reason") or "bad pair", ""
        missing = [name for name in UBOE_REQUIRED_FIELDS if pair.get(name) in ("", None)]
        if missing:
            return f"not executable: missing {','.join(missing)}", ""
        port = self.port_planner.port(pair, index)
        if not self.port_planner.port_in_range(port):
            return f"port out of range: {port}", port
        return "", port

    def listener(self, pair, port):
        # Server side has no -S (only the client peers at the server ip). Matches wiki.
        cmd = (f"urma_perftest {self.mode} -d {pair.get('server_udma')} "
               f"-P {int(port)} --uboe "
               f"{self.opts_for_pair()} "
               f"--sip {pair.get('server_bond_ip')} --dip {pair.get('client_bond_ip')}").strip()
        return self.command_item("listener", pair, port, pair.get("server_target", ""), cmd)

    def client(self, pair, port):
        cmd = (f"urma_perftest {self.mode} -d {pair.get('client_udma')} "
               f"-S {pair.get('server_bond_ip')} -P {int(port)} --uboe "
               f"{self.opts_for_pair()} "
               f"--sip {pair.get('client_bond_ip')} --dip {pair.get('server_bond_ip')}").strip()
        return self.command_item("client", pair, port, pair.get("client_target", ""), cmd)

    def opts_for_pair(self):
        tokens = shlex.split(self.opts)
        if self.single:
            tokens = [token for token in tokens if token != "-B"]
        return " ".join(shlex.quote(token) for token in tokens)

    def command_item(self, role, pair, port, target, cmd):
        host_by_role = {"listener": pair.get("server_bond_ip"), "client": pair.get("client_bond_ip")}
        host = host_by_role.get(role, "")
        ready = all(pair.get(name) not in ("", None) for name in UBOE_REQUIRED_FIELDS)
        return command_result(target=f"{role}:{target}", host=host, cmd=cmd,
                              ok=bool(pair.get("ok")), rc=0 if pair.get("ok") else 2,
                              error_type="" if pair.get("ok") else "config", role=role,
                              command_target=target, uboe=True,
                              **{**pair_context(pair), "uboe_port": port, "uboe_mode": self.mode,
                                 "uboe_duration": self.duration, "ready": ready,
                                 "reason": "ready" if ready else "uboe fields not resolved"})

    def plan_commands(self, pairs):
        commands = []
        for index, pair in enumerate(pairs):
            reason, port = self.not_ready_reason(pair, index)
            if reason:
                commands.append(command_result(target=pair_target_text(pair), cmd="", ok=False, rc=2,
                                               error_type="config", stderr=reason, role="not_executable",
                                               command_target=pair.get("target", ""),
                                               **pair_context(pair)))
                continue
            commands.append(self.listener(pair, port))
            commands.append(self.client(pair, port))
        return commands


def pair_target_text(pair):
    client = pair.get("client_target") or ""
    server = pair.get("server_target") or ""
    return f"not_executable:{client}->{server}" if client or server else pair.get("target", "")
