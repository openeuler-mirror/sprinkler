"""URMA bandwidth result parsing."""

import re
from decimal import Decimal, ROUND_HALF_UP

from sprinkler.auto.bandwidth.common import EMPTY_FIELDS, PAIR_DEFAULTS, PAIR_CONTEXT_FIELDS, command_result, text_value, pair_context, pair_target, compact_target_text, pair_side_text, is_xboard_pair, has_link_distinction, display_reason, display_link, link_suffix, args_link_mode, grouped, execution_host, role_eid
from sprinkler.base.results import CommandResultView

EMPTY_BW_SUMMARY = {"bw_samples": 0, "bw_average": 0.0, "bw_min": 0.0, "bw_max": 0.0, "bw_positive": False}
EMPTY_LAT_SUMMARY = {"lat_samples": 0, "lat_min": 0.0, "lat_max": 0.0, "lat_median": 0.0, "lat_avg": 0.0}
BANDWIDTH_NUMBER_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)")
BANDWIDTH_INTEGER_RE = re.compile(r"[+-]?\d+")
BANDWIDTH_HEADER_RE = re.compile(r"\bBW(?:_|\s+)average\b", re.IGNORECASE)
LATENCY_HEADER_RE = re.compile(r"\bt_(?:min|avg|median|max)\s*\[us\]", re.IGNORECASE)
URMA_FATAL_RE = re.compile(r"(unrecognized option|Usage:\s*urma_perftest|ERROR\b|Failed to|No such|Invalid)",
                           re.IGNORECASE)
URMA_BENIGN_WARNING_RE = re.compile(r"(setlocale:.*cannot change locale|Large message requested and CQ moderation "
                                    r"enabled|It can lead to inaccurate results)", re.IGNORECASE)
URMA_STOP_DIAGNOSTIC_RE = re.compile(r"(run_once_bw_infinite|prepare and run infinite|run test:\s*\d+|"
                                     r"read data during sock_sync_data|sync time error|unimport jetty|poll jfc)",
                                     re.IGNORECASE)
BW_UNIT_RE = re.compile(r"--bw_unit\s+(\w+)", re.IGNORECASE)
DISPLAY_DECIMAL = Decimal("0.1")
LATENCY_DISPLAY_DECIMAL = Decimal("0.01")
BANDWIDTH_DISPLAY_UNIT = "[MB/s]"
LATENCY_DISPLAY_UNIT = "[us]"


def bw_display_unit(opts_text=None):
    """Parse --bw_unit from urma/uboe opts, return display unit suffix like `[GB/s]`.

    Defaults to `[MB/s]` when no --bw_unit is present or the value is unrecognized.
    """
    if not opts_text:
        return BANDWIDTH_DISPLAY_UNIT
    match = BW_UNIT_RE.search(str(opts_text))
    if not match:
        return BANDWIDTH_DISPLAY_UNIT
    unit = match.group(1).upper()
    if unit in ("GB", "MB", "KB"):
        return f"[{unit}/s]"
    return BANDWIDTH_DISPLAY_UNIT

DEFAULT_SAMPLE_TAIL = 10
DEFAULT_START_STAGGER_SECONDS = 0.2
DEFAULT_LISTENER_READY_SECONDS = 2.0

def bandwidth_summary(text, tail=None):
    samples = []
    armed = False
    for raw in str(text or "").replace("\r", "").splitlines():
        line = raw.split(" | ", 1)[1] if " | " in raw else raw
        if BANDWIDTH_HEADER_RE.search(line):
            armed = True
            continue
        value = bandwidth_sample_average(line) if armed else None
        if value is not None:
            samples.append(value)
    if len(samples) > 1:
        samples = samples[:-1]
    if tail:
        samples = samples[-int(tail):]
    if not samples:
        return dict(EMPTY_BW_SUMMARY)
    return {"bw_samples": len(samples), "bw_average": round(sum(samples) / len(samples), 3),
            "bw_min": round(min(samples), 3), "bw_max": round(max(samples), 3),
            "bw_positive": any(value > 0 for value in samples)}

def bandwidth_sample_average(line):
    parts = [token.strip("[](),;") for token in str(line or "").strip().split()]
    parts = [part for part in parts if BANDWIDTH_NUMBER_RE.fullmatch(part)]
    if (len(parts) < 5 or not BANDWIDTH_INTEGER_RE.fullmatch(str(parts[0] or ""))
            or not BANDWIDTH_INTEGER_RE.fullmatch(str(parts[1] or ""))):
        return None
    return float(parts[3])

def latency_summary(text, tail=None):
    samples = []
    armed = False
    for raw in str(text or "").replace("\r", "").splitlines():
        line = raw.split(" | ", 1)[1] if " | " in raw else raw
        if LATENCY_HEADER_RE.search(line):
            armed = True
            continue
        value = latency_sample(line) if armed else None
        if value is not None:
            samples.append(value)
    if tail:
        samples = samples[-int(tail):]
    if not samples:
        return dict(EMPTY_LAT_SUMMARY)
    count = len(samples)
    return {"lat_samples": count,
            "lat_min": round(min(sample[0] for sample in samples), 3),
            "lat_max": round(max(sample[1] for sample in samples), 3),
            "lat_median": round(sum(sample[2] for sample in samples) / count, 3),
            "lat_avg": round(sum(sample[3] for sample in samples) / count, 3)}

def latency_sample(line):
    parts = [token for token in str(line or "").strip().split()]
    if (len(parts) < 7 or not BANDWIDTH_INTEGER_RE.fullmatch(str(parts[0] or ""))
            or not BANDWIDTH_INTEGER_RE.fullmatch(str(parts[1] or ""))):
        return None
    try:
        return tuple(float(part) for part in parts[2:7])
    except ValueError:
        return None

def fatal_output_reason(*texts, has_bandwidth=False):
    for line in "\n".join(text_value(text) for text in texts).replace("\r", "").splitlines():
        if URMA_BENIGN_WARNING_RE.search(line):
            continue
        if URMA_FATAL_RE.search(line):
            if has_bandwidth and URMA_STOP_DIAGNOSTIC_RE.search(line):
                continue
            return line.strip()
    return ""

def machine_pair(pair):
    if not isinstance(pair, dict):
        return "local"
    client = pair.get("client_machine") or pair.get("client_cpu_target") or pair.get("client_target") or "client"
    server = pair.get("server_machine") or pair.get("server_cpu_target") or pair.get("server_target") or "server"
    return f"{client}->{server}"

def fail_pair(pair, reason, link_mode="all", sample_tail=DEFAULT_SAMPLE_TAIL):
    result = empty_process_result(reason, 2)
    return traffic_pair_result(pair, "urma_perftest", False, result, reason, link_mode=link_mode,
                               sample_tail=sample_tail)

def empty_process_result(reason="process was not started", rc=255):
    return command_result(target="urma", cmd="urma_perftest", ok=False, rc=rc, error_type="process",
                          stderr=reason, backend="urma")

def traffic_pair_result(pair, command, ok, client_result, reason, listener_result=None, link_mode="all",
                        sample_tail=DEFAULT_SAMPLE_TAIL):
    client_stdout = text_value(client_result.get("stdout"))
    server_stdout = text_value(listener_result.get("stdout") if listener_result else "")
    client_stderr = text_value(client_result.get("stderr"))
    server_stderr = text_value(listener_result.get("stderr") if listener_result else "")
    stdout = side_outputs(("client", client_stdout), ("server", server_stdout))
    stderr = side_outputs(("client", client_stderr), ("server", server_stderr))
    client_bw = bandwidth_summary(client_stdout, sample_tail)
    server_bw = bandwidth_summary(server_stdout, sample_tail)
    bw = merge_bw(client_bw, server_bw)
    client_lat = latency_summary(client_stdout, sample_tail)
    server_lat = latency_summary(server_stdout, sample_tail)
    lat = merge_latency(client_lat, server_lat)
    latency_mode = bool(lat["lat_samples"])
    fatal_reason = fatal_output_reason(client_stdout, server_stdout, client_stderr, server_stderr,
                                       has_bandwidth=bw["bw_positive"])
    has_traffic = bool(lat["lat_samples"]) if latency_mode else bool(bw["bw_positive"])
    command_ok = (bool(ok) or has_traffic) and not fatal_reason
    traffic_ok = command_ok and has_traffic
    final_reason = fatal_reason or reason
    if command_ok:
        if traffic_ok:
            final_reason = "latency ok" if latency_mode else "bandwidth ok"
        else:
            final_reason = "no latency sample" if latency_mode else "no positive bandwidth sample"
    if latency_mode:
        traffic_fields = {**pair_context(pair), "reason": final_reason,
                          "flow_ok": command_ok, "lat_ok": bool(lat["lat_samples"]),
                          **lat_fields("", lat), **lat_fields("client", client_lat),
                          **lat_fields("server", server_lat)}
    else:
        traffic_fields = {**pair_context(pair), "reason": final_reason,
                          "flow_ok": command_ok, "bw_ok": bool(bw["bw_positive"]),
                          **bw_fields("", bw), **bw_fields("client", client_bw),
                          **bw_fields("server", server_bw)}
    if is_xboard_pair(pair):
        traffic_fields.update(machine_pair=machine_pair(pair), link_mode=link_mode)
    return command_result(target=pair_target(pair), host=execution_host(pair), cmd=command, ok=traffic_ok,
                          rc=0 if traffic_ok else int(client_result.get("rc") or 1),
                          error_type="" if traffic_ok else ("latency" if latency_mode else "bandwidth"
                                                            if command_ok else "urma"),
                          stdout=stdout, stderr=stderr, elapsed=client_result.get("elapsed") or 0,
                          backend="urma", **traffic_fields)

def merge_bw(client_bw, server_bw):
    values = [item for item in (client_bw, server_bw) if item.get("bw_samples")]
    samples = sum(item["bw_samples"] for item in values)
    if not values:
        return dict(EMPTY_BW_SUMMARY)
    weighted = sum(item["bw_average"] * item["bw_samples"] for item in values) / samples
    return {"bw_samples": samples, "bw_average": round(weighted, 3),
            "bw_min": min(item["bw_min"] for item in values), "bw_max": max(item["bw_max"] for item in values),
            "bw_positive": any(item.get("bw_positive") for item in values)}

def bw_fields(prefix, summary):
    prefix = f"{prefix}_" if prefix else ""
    return {f"{prefix}bw_samples": summary["bw_samples"], f"{prefix}bw_average": summary["bw_average"],
            f"{prefix}bw_min": summary["bw_min"], f"{prefix}bw_max": summary["bw_max"]}

def merge_latency(client_lat, server_lat):
    values = [item for item in (client_lat, server_lat) if item.get("lat_samples")]
    samples = sum(item["lat_samples"] for item in values)
    if not values:
        return dict(EMPTY_LAT_SUMMARY)
    return {"lat_samples": samples,
            "lat_min": min(item["lat_min"] for item in values),
            "lat_max": max(item["lat_max"] for item in values),
            "lat_median": round(sum(item["lat_median"] * item["lat_samples"] for item in values) / samples, 3),
            "lat_avg": round(sum(item["lat_avg"] * item["lat_samples"] for item in values) / samples, 3)}

def lat_fields(prefix, summary):
    prefix = f"{prefix}_" if prefix else ""
    return {f"{prefix}lat_samples": summary["lat_samples"], f"{prefix}lat_min": summary["lat_min"],
            f"{prefix}lat_max": summary["lat_max"], f"{prefix}lat_median": summary["lat_median"],
            f"{prefix}lat_avg": summary["lat_avg"]}

def side_outputs(*items):
    return "\n".join(f"--- {side} ---\n{text}" for side, text in items if text)

class UrmaBandwidthResultView:
    def __init__(self, formatter, bw_unit=None):
        self.command_view = CommandResultView(formatter)
        self.bw_unit = str(bw_unit or BANDWIDTH_DISPLAY_UNIT)

    def summary(self, result: dict, level: str):
        if "bandwidth_phase" in result or "bw_average" in result or "lat_samples" in result:
            return self.traffic_summary(result)
        return self.command_view.summary(result, level)

    def output_blocks(self, result: dict, ok: bool, show_output: bool):
        blocks = self.command_view.output_blocks(result, ok, show_output)
        return [(level, prefix, text, False) for level, prefix, text, _show in blocks if "[detail]" not in prefix]

    def traffic_summary(self, result):
        kind = str(result.get("pair_kind") or result.get("kind") or "")
        target = str(result.get("target") or "")
        if kind in {"intra_h2d", "intra_d2h"}:
            target = compact_target_text(result) or target
        if not kind and target.startswith("xboard:"):
            kind = "xboard_d2d"
        for prefix in (f"{kind}:", "xboard:"):
            if prefix != ":" and target.startswith(prefix):
                target = target[len(prefix):]
                break
        link_text = str(result.get("link") or "").lower()
        link_mode = str(result.get("link_mode") or "").lower()
        phase = str(result.get("bandwidth_phase") or "").lower()
        label = traffic_label(kind, link_text, link_mode, phase)
        parts = [f"[{label} {target}]"]
        link = link_suffix(link_text)
        link = link or (link_mode if link_mode in {"l0", "l1", "ubg", "all"} else "")
        if link:
            parts.append(f"link={link}")
        ping_ok = bool(result.get("ping", result.get("execution_status") != "ping_failed"))
        flow_ok = self.command_view.formatter.result_ok(result)
        parts.extend([f"ping={'PASS' if ping_ok else 'FAIL'}", f"flow={'PASS' if flow_ok else 'FAIL'}"])
        for side in ("client", "server"):
            if result.get(f"{side}_lat_samples"):
                values = tuple(str(Decimal(str(result.get(f"{side}_lat_{name}") or 0))
                                   .quantize(LATENCY_DISPLAY_DECIMAL, rounding=ROUND_HALF_UP))
                               for name in ("avg", "min", "median", "max"))
                parts.append(f"{side}(avg/min/median/max)={'/'.join(values)}{LATENCY_DISPLAY_UNIT}")
            elif result.get(f"{side}_bw_samples"):
                values = tuple(str(Decimal(str(result.get(f"{side}_bw_{name}") or 0))
                                   .quantize(DISPLAY_DECIMAL, rounding=ROUND_HALF_UP))
                               for name in ("average", "min", "max"))
                parts.append(f"{side}(mean/min/max)={'/'.join(values)}{self.bw_unit}")
        if not flow_ok and result.get("reason"):
            reason = str(result.get("reason"))
            if reason == "no positive bandwidth sample":
                reason = "未获取到有效带宽数据"
            elif reason == "no latency sample":
                reason = "未获取到有效时延数据"
            parts.append(f"reason={reason}")
        return " ".join(parts)

def traffic_label(kind, link_text="", link_mode="", phase=""):
    if kind == "xboard_d2d":
        if "l2" in link_text or link_mode == "ubg" or phase == "ubg":
            return "UBG D2D"
        return "出框口D2D"
    labels = {"intra_d2d": "X轴D2D", "intra_h2d": "H2D", "intra_d2h": "D2H",
              "uboe_d2d": "UBOE打流", "uboe": "UBOE打流"}
    return labels.get(kind, kind or "打流")
