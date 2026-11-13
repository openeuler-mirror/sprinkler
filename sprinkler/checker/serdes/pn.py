"""SerDes PN polarity check for the local NPU set.

One command is issued per (port, direction), executed on the NPU OS through
``npu_os_command``.  ``tx`` and ``rx`` are always queried together, so every
port gets both directions::

    /home/serdestool.bin pn -i <udie> -d <tx|rx> -s m<macro>d<lane_start> -n <lanes>

Tool output::

    serdes[0] macro[0] lane[0] get pn tx ctl_en is 1.

Port mapping inside one udie (40 lanes = 10 macros x 4 lanes)::

    x2 mode: 20 ports x 2 lanes -> port p -> macro p//2, lane_start (p%2)*2
    x4 mode: 10 ports x 4 lanes -> port p -> macro p,    lane_start 0

``-udie`` is passed straight to the tool ``-i`` option (the sample
``-i 0`` prints ``serdes[0]``).  The check only looks at NPUs owned by the
local CPU host (``/proc/asdrv_ub`` via ``get_host_and_device_pairs``); remote
cluster nodes are out of scope for now.  ``serdestool.bin`` is expected at
``/home/serdestool.bin`` on the NPU OS and is uploaded from the CPU OS when
missing (see :mod:`sprinkler.checker.serdes.tool`).

The result is rendered as a matrix: rows are local NPU ranks, columns are
``P<port>`` with ``tx``/``rx`` sub-columns, and each cell holds the per-lane
``ctl_en`` bits in lane order (``L<lane_start> .. L<lane_start+lanes-1>``).
"""

import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from sprinkler.auto.bandwidth.common.route_conf import RouteConf
from sprinkler.base.log import RunLog
from sprinkler.base.ranges import parse_range
from sprinkler.checker.serdes.tool import DEFAULT_NPU_PATH, ensure_tools
from sprinkler.config.host_device_pairs import get_host_and_device_pairs
from sprinkler.connect.npu_access import npu_os_command
from sprinkler.utils.utils import positive_int_or_default

CHECK_ID = "serdes_pn"

#: port mode -> (port count, lanes per port)
PORT_MODES = {
    "x2": {"ports": 20, "lanes": 2},
    "x4": {"ports": 10, "lanes": 4},
}
DIRECTIONS = ("tx", "rx")
DEFAULT_SERDES_BIN = DEFAULT_NPU_PATH
DEFAULT_TIMEOUT = 30
DEFAULT_PARALLEL = 8

PN_LINE_RE = re.compile(
    r"serdes\[(?P<serdes>\d+)\]\s+macro\[(?P<macro>\d+)\]\s+lane\[(?P<lane>\d+)\]\s+"
    r"get\s+pn\s+(?P<direction>tx|rx)\s+ctl_en\s+is\s+(?P<value>[01])",
    re.IGNORECASE,
)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def port_layout(mode, port):
    """Return ``(macro, lane_start, lanes)`` for one port of a udie."""
    spec = PORT_MODES[mode]
    lanes = spec["lanes"]
    port = int(port)
    if mode == "x2":
        # Two X2 ports share one X4 macro: P0 -> d0, P1 -> d2.
        return port // 2, (port % 2) * lanes, lanes
    return port, 0, lanes


def resolve_ports(mode, spec):
    """Expand ``-ports`` (``all`` / ``0-3,5``) into a sorted, validated list."""
    limit = PORT_MODES[mode]["ports"]
    text = str(spec or "").strip().lower()
    if text in ("", "all"):
        return list(range(limit))
    try:
        ports = parse_range(text)
    except ValueError as exc:
        raise ValueError(f"bad -ports {spec!r}: {exc}") from exc
    if not ports:
        raise ValueError("-ports selected no port")
    invalid = [port for port in ports if port < 0 or port >= limit]
    if invalid:
        raise ValueError(
            f"port {invalid[0]} out of range for {mode.upper()} mode "
            f"(valid: 0-{limit - 1})")
    return sorted(set(ports))


def build_command(serdes_bin, udie, direction, macro, lane_start, lanes):
    return (f"{serdes_bin} pn -i {int(udie)} -d {direction} "
            f"-s m{int(macro)}d{int(lane_start)} -n {int(lanes)}")


def parse_pn_output(text, macro, lane_start, lanes, direction):
    """Parse one tool output into per-lane bits.

    Handles both absolute lane numbering (``lane[2]`` for ``-s m0d2``) and
    numbering relative to the ``-d`` start (``lane[0]`` for ``-s m0d2``),
    because the tool documentation only covers the ``d0`` case.
    """
    cleaned = ANSI_RE.sub("", str(text or "")).replace("\r", "")
    entries = {}
    serdes_indexes = set()
    other_macros = set()
    for match in PN_LINE_RE.finditer(cleaned):
        if match.group("direction").lower() != direction:
            continue
        found_macro = int(match.group("macro"))
        if found_macro != int(macro):
            other_macros.add(found_macro)
            continue
        entries[int(match.group("lane"))] = int(match.group("value"))
        serdes_indexes.add(int(match.group("serdes")))

    expected = list(range(int(lane_start), int(lane_start) + int(lanes)))
    values = [entries.get(lane) for lane in expected]
    if (any(value is None for value in values)
            and all(lane in entries for lane in range(int(lanes)))):
        # Tool numbered lanes from the -d start instead of from the macro.
        expected = list(range(int(lanes)))
        values = [entries.get(lane) for lane in expected]

    return {
        "values": values,
        "missing": [lane for lane, value in zip(expected, values) if value is None],
        "serdes": sorted(serdes_indexes),
        "other_macros": sorted(other_macros),
        "matched": len(entries),
    }


def npu_ssh_host(values):
    """Return the ssh host of the NPU, tolerating IPv4 static pairs."""
    raw = str(values[1]).strip() if len(values or []) > 1 else ""
    return RouteConf.format_eid32(raw) or raw


def build_local_npu_items(args, run_log=None):
    """Local NPU ranks and their ssh hosts, filtered by ``-rank``."""
    pairs = get_host_and_device_pairs()
    rank_spec = str(getattr(args, "rank", "") or "").strip()
    selected = parse_range(rank_spec) if rank_spec else sorted(pairs)

    items = []
    for rank in selected:
        host = npu_ssh_host(pairs.get(rank) or [])
        if not host:
            if run_log is not None:
                run_log.info(f"NPU{rank} skipped: npu eid missing")
            continue
        items.append({"rank": int(rank), "target": f"NPU{rank}", "execute_eid": host})
    return items


class PnCheckRunner:
    def __init__(self, args, run_log=None, execute=None):
        self.args = args
        self.run_log = run_log
        self.execute = execute or self._execute_remote

        self.udie = int(getattr(args, "udie", 0) or 0)
        if self.udie not in (0, 1):
            raise ValueError(f"bad -udie {self.udie}: expected 0 or 1")

        self.mode = str(getattr(args, "port_mode", "x2") or "x2").strip().lower()
        if self.mode not in PORT_MODES:
            raise ValueError(f"bad -port-mode {self.mode!r}: expected x2 or x4")

        self.ports = resolve_ports(self.mode, getattr(args, "pn_ports", "all"))
        self.directions = list(DIRECTIONS)
        self.serdes_bin = str(getattr(args, "serdes_bin", DEFAULT_SERDES_BIN)
                              or DEFAULT_SERDES_BIN)
        self.timeout = positive_int_or_default(
            getattr(args, "serdes_timeout", DEFAULT_TIMEOUT), DEFAULT_TIMEOUT, "-serdes-timeout")

    @property
    def lanes(self):
        return PORT_MODES[self.mode]["lanes"]

    def run(self, items):
        parallel = positive_int_or_default(
            getattr(self.args, "parallel", DEFAULT_PARALLEL), DEFAULT_PARALLEL, "-parallel")
        workers = max(1, min(parallel, len(items)))
        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                batches = list(pool.map(self.run_npu, items))
        else:
            batches = [self.run_npu(item) for item in items]
        return [row for batch in batches for row in batch]

    def run_npu(self, item):
        if item.get("error"):
            return self.failure_rows(item, item["error"])

        rows = []
        for port in self.ports:
            macro, lane_start, lanes = port_layout(self.mode, port)
            for direction in self.directions:
                command = build_command(self.serdes_bin, self.udie, direction,
                                        macro, lane_start, lanes)
                outcome = self.execute(item["execute_eid"], command, self.timeout) or {}
                rows.append(self.build_row(item, port, macro, lane_start, lanes,
                                           direction, command, outcome))
        return rows

    def failure_rows(self, item, reason):
        """One failed row per requested cell, so the matrix keeps its shape."""
        rows = []
        for port in self.ports:
            macro, lane_start, lanes = port_layout(self.mode, port)
            for direction in self.directions:
                rows.append(self.build_row(item, port, macro, lane_start, lanes, direction,
                                           "", {"rc": 1, "reason": reason, "elapsed": 0}))
        return rows

    def build_row(self, item, port, macro, lane_start, lanes, direction, command, outcome):
        stdout = str(outcome.get("stdout") or "")
        stderr = str(outcome.get("stderr") or "")
        parsed = parse_pn_output("\n".join([stdout, stderr]), macro=macro,
                                 lane_start=lane_start, lanes=lanes, direction=direction)
        rc = int(outcome.get("rc", 0) or 0)

        reason = str(outcome.get("reason") or "").strip()
        if not reason:
            if rc != 0:
                detail = _first_line(stderr or stdout)
                reason = f"rc={rc}" + (f": {detail}" if detail else "")
            elif parsed["missing"]:
                reason = "lane(s) %s missing in output" % ",".join(str(lane) for lane in parsed["missing"])
            elif parsed["other_macros"] and not parsed["matched"]:
                reason = "macro %s missing in output" % ",".join(str(m) for m in parsed["other_macros"])

        values = parsed["values"]
        bits = "".join("?" if value is None else str(value) for value in values)
        return {
            "target": item.get("target") or f"NPU{item['rank']}",
            "npu": int(item["rank"]),
            "udie": self.udie,
            "port": int(port),
            "port_mode": self.mode,
            "dir": direction,
            "macro": int(macro),
            "lane_start": int(lane_start),
            "lanes": int(lanes),
            "values": ",".join("" if value is None else str(value) for value in values),
            "bits": bits,
            "serdes_index": ",".join(str(index) for index in parsed["serdes"]),
            "ok": not reason,
            "rc": rc,
            "error_type": "" if not reason else ("command" if rc else "parse"),
            "reason": reason or "ready",
            "cmd": command,
            "execute_eid": item.get("execute_eid", ""),
            "stdout": stdout.strip(),
            "stderr": stderr.strip(),
            "elapsed": round(float(outcome.get("elapsed") or 0), 3),
        }

    def plan_commands(self, item):
        lines = []
        for port in self.ports:
            macro, lane_start, lanes = port_layout(self.mode, port)
            for direction in self.directions:
                command = build_command(self.serdes_bin, self.udie, direction,
                                        macro, lane_start, lanes)
                lines.append(f"[dry-run] {item['target']} {command}")
        return lines

    def _execute_remote(self, eid, command, timeout):
        started = time.time()
        try:
            completed = subprocess.run(
                npu_os_command(eid, command), shell=True, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout)
            return {"rc": completed.returncode, "stdout": completed.stdout,
                    "stderr": completed.stderr, "elapsed": time.time() - started}
        except subprocess.TimeoutExpired:
            return {"rc": 124, "stdout": "", "stderr": f"timeout after {timeout}s",
                    "elapsed": time.time() - started}
        except Exception as exc:
            return {"rc": 125, "stdout": "", "stderr": str(exc),
                    "elapsed": time.time() - started}


def _first_line(text):
    for line in str(text or "").strip().splitlines():
        if line.strip():
            return line.strip()
    return ""


def render_matrix(rows, ranks, ports, directions, udie, mode):
    """Render the PN polarity matrix (rows = NPU, columns = port + direction)."""
    lanes = PORT_MODES[mode]["lanes"]
    cells = {}
    for row in rows:
        value = row.get("bits") if row.get("ok") else "--"
        key = (int(row.get("npu")), int(row.get("port")), str(row.get("dir")))
        cells[key] = value or "--"

    cell_w = max([lanes, 2] + [len(value) for value in cells.values()])
    port_w = cell_w * len(directions) + (len(directions) - 1)
    npu_w = max([len(f"NPU{rank}") for rank in ranks] + [len("NPU")])

    border = "+" + "-" * (npu_w + 2) + ("+" + "-" * (port_w + 2)) * len(ports) + "+"
    lines = [
        f"PN polarity matrix (udie={udie}, mode={mode}, lanes/port={lanes}, "
        f"directions={','.join(directions)})",
        border,
        "| " + "NPU".ljust(npu_w) + " "
        + "".join(f"| {('P' + str(port)).center(port_w)} " for port in ports) + "|",
        "| " + " " * npu_w + " "
        + "".join("| " + " ".join(direction.rjust(cell_w) for direction in directions) + " "
                  for _ in ports) + "|",
        border,
    ]
    for rank in ranks:
        values = [
            " ".join(cells.get((int(rank), int(port), direction), "--").rjust(cell_w)
                     for direction in directions)
            for port in ports
        ]
        lines.append("| " + f"NPU{rank}".ljust(npu_w) + " "
                     + "".join(f"| {value} " for value in values) + "|")
    lines.append(border)
    return lines


def pn_check_execute(args, npu_items=None, execute=None, manage_tools=None):
    """``-pn-check`` entry point. Returns 0 when every query succeeded.

    ``npu_items`` / ``execute`` / ``manage_tools`` are injection seams for
    tests; a normal CLI run resolves targets and manages the tool itself.
    """
    run_log = RunLog(args, default_check_id=CHECK_ID)
    run_log.info(f"log_dir={run_log.path or 'disabled'}")

    try:
        runner = PnCheckRunner(args, run_log=run_log, execute=execute)
    except Exception as exc:
        return run_log.fail_error(CHECK_ID, exc)

    lanes = PORT_MODES[runner.mode]["lanes"]
    run_log.info(f"pn_plan udie={runner.udie} mode={runner.mode} lanes_per_port={lanes} "
                 f"port_total={len(runner.ports)} direction_total={len(runner.directions)}")
    run_log.info(f"serdes_bin={runner.serdes_bin}")
    run_log.info("port_map " + " ".join(
        f"P{port}=m{macro}d{lane_start}"
        for port in runner.ports
        for macro, lane_start, _lanes in [port_layout(runner.mode, port)]))

    try:
        items = list(npu_items) if npu_items is not None else build_local_npu_items(args, run_log)
    except Exception as exc:
        return run_log.fail_error(CHECK_ID, exc)

    run_log.info(f"local_npu_total={len(items)}")
    if not items:
        run_log.fail("no local NPU with an EID found; run as root on a CPU host with /proc/asdrv_ub")
        run_log.write_results([])
        run_log.auto_details(CHECK_ID, 0, 0)
        run_log.finish_log_dir()
        return 1

    if getattr(args, "pn_dry_run", False):
        total = 0
        for item in items:
            for line in runner.plan_commands(item):
                run_log.info(line)
                total += 1
        run_log.info(f"dry_run npu_total={len(items)} command_total={total}")
        run_log.finish_log_dir()
        return 0

    if manage_tools is None:
        manage_tools = npu_items is None
    if manage_tools:
        ensure_tools(items, args, runner.serdes_bin, run_log)

    rows = runner.run(items)
    run_log.write_results(rows)

    ranks = [int(item["rank"]) for item in items]
    for line in render_matrix(rows, ranks, runner.ports, runner.directions, runner.udie, runner.mode):
        run_log.info(line)

    for item in items:
        npu_rows = [row for row in rows if row["npu"] == int(item["rank"])]
        passed = sum(1 for row in npu_rows if row.get("ok"))
        level = "PASS" if npu_rows and passed == len(npu_rows) else "FAIL"
        run_log.log(level, f"{item['target']} ok={passed} total={len(npu_rows)}")

    failed = [row for row in rows if not row.get("ok")]
    for row in failed[:50]:
        run_log.fail(f"{row.get('target')} P{row.get('port')}/{row.get('dir')} "
                     f"reason={row.get('reason')}")
    if len(failed) > 50:
        run_log.fail(f"... {len(failed) - 50} more failures, see results.json")

    passed = len(rows) - len(failed)
    run_log.info(f"pn_summary npu_total={len(items)} port_total={len(runner.ports)} "
                 f"command_total={len(rows)} pass={passed} fail={len(failed)}")
    run_log.auto_details(CHECK_ID, passed, len(rows))
    run_log.finish_log_dir()
    return 0 if rows and passed == len(rows) else 1
