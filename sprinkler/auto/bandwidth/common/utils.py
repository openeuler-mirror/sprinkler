"""Shared pure tools, mode parsing, and default paths for bandwidth planning."""

DEFAULT_ROUTE_CONF = "/lib/route.conf"
DEFAULT_ROOTINFO = "/etc/hccl_rootinfo.json"

DEFAULT_MODE_TEXT = "h2d,d2d"
INTRA_MODES = ("h2d", "d2h", "d2d")
XBOARD_MODES = ("xd2d", "ubg")
UBOE_MODES = ("uboe", "uboe-xboard")
MODE_VALUES = {*INTRA_MODES, "all", *XBOARD_MODES, *UBOE_MODES}
UBOE_XBOARD_MODE = "uboe-xboard"
LINK_SUFFIXES = ("l0", "l1", "l2")

EMPTY_FIELDS = ("client_rank server_rank client_target server_target client_eid32 server_eid32 "
                "client_execute_eid server_execute_eid "
                "client_eid_idx server_eid_idx client_udma server_udma "
                "client_bond_ip server_bond_ip "
                "link reason cmd target host backend error_type stdout stderr")

PAIR_DEFAULTS = {name: "" for name in EMPTY_FIELDS.split()}
PAIR_DEFAULTS.update(ok=True, rc=0, cmd="eid-pairs", reason="ready", port=0, elapsed=0.0, backend="planner")

PAIR_CONTEXT_FIELDS = ("scope runner client_rank server_rank client_target server_target client_machine server_machine "
                       "client_eid32 server_eid32 client_execute_eid server_execute_eid "
                       "client_bond_ip server_bond_ip link").split()

TARGET_SELECTOR_FIELDS = ("sp", "ca", "node", "xlsx", "ip")
TARGET_SELECTOR_WITH_PLAN_FIELDS = (*TARGET_SELECTOR_FIELDS, "remote_plan")

XBOARD_PAIR_REQUIRED_FIELDS = ("client_machine", "server_machine", "client_rank", "server_rank", "client_eid32",
                               "server_eid32", "link")

XBOARD_PING_REQUIRED_FIELDS = ("client_machine", "server_machine", "client_cpu_host", "client_execute_eid",
                               "client_eid32", "server_eid32")

def _is_ipv4(text):
    """判断字符串是否为合法IPv4地址(如 192.168.2.199)。供静态IPv4回退场景使用。"""
    parts = str(text or "").strip().split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit() or not 0 <= int(part) <= 255:
            return False
    return True

def text_value(value):
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)

def int_value(value, default=None):
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text = str(value).strip()
    if text.lstrip("-").isdecimal():
        return int(text)
    return default

def unique_ints(values):
    return sorted({item for value in values or [] for item in [int_value(value)] if item is not None})

def grouped(items, key_func):
    groups = {}
    for item in items:
        groups.setdefault(key_func(item), []).append(item)
    return groups

def has_target_selector(args, include_remote_plan=False):
    fields = TARGET_SELECTOR_WITH_PLAN_FIELDS if include_remote_plan else TARGET_SELECTOR_FIELDS
    return any(str(getattr(args, name, "") or "").strip() for name in fields)

def mode_list(value):
    text = str(value or DEFAULT_MODE_TEXT).strip().lower()
    parts = [part.strip() for part in text.split(",") if part.strip()] or list(DEFAULT_MODE_TEXT.split(","))
    modes = []
    for part in parts:
        if part not in MODE_VALUES:
            raise ValueError("-mode must be h2d, d2h, d2d, all, xd2d, ubg, uboe, uboe-xboard, or comma combinations")
        modes.extend(INTRA_MODES if part == "all" else (part,))

    unique_modes = list(dict.fromkeys(modes))
    if any(mode in XBOARD_MODES for mode in unique_modes) and len(unique_modes) > 1:
        raise ValueError("-mode xd2d/ubg cannot be combined with other modes")
    if any(mode in UBOE_MODES for mode in unique_modes) and len(unique_modes) > 1:
        raise ValueError("-mode uboe cannot be combined with other modes")
    return unique_modes

def normalize_link_mode(value):
    text = str(value or "all").strip().lower()
    if text == "both":
        return "all"
    if text in {"all", "l0", "l1"}:
        return text
    raise ValueError("-link-mode must be all, both, l0, or l1; use -mode ubg for net_layer=2")

def xboard_link_mode(mode, value="all"):
    modes = mode if isinstance(mode, (list, tuple)) else mode_list(mode)
    link_mode = normalize_link_mode(value)
    if list(modes) != ["ubg"]:
        return link_mode
    if link_mode != "all":
        raise ValueError("-mode ubg does not use -link-mode")
    return "ubg"

def args_mode_list(args):
    return mode_list(getattr(args, "mode", "") if args is not None else "")

def is_latency_mode(value):
    return str(value or "").strip().endswith("_lat")

def args_link_mode(args):
    return xboard_link_mode(args_mode_list(args), getattr(args, "link_mode", "all") if args is not None else "all")

def link_names(value):
    if str(value or "").strip().lower() == "ubg":
        return ("L2",)
    mode = normalize_link_mode(value)
    return {"l0": ("L0",), "l1": ("L1",)}.get(mode, ("L0", "L1"))

def link_suffix(value, default=""):
    text = str(value or "").strip().lower()
    return next((name for name in LINK_SUFFIXES if text.endswith(name)), default)

def pair_matches_link_mode(pair, value):
    mode = str(value or "all").strip().lower()
    if mode != "ubg":
        mode = normalize_link_mode(mode)
    link = str((pair or {}).get("link") or "").strip().upper()
    if mode == "all":
        return not link.endswith("L2") and ":L2" not in link
    wanted = {"ubg": "L2"}.get(mode, mode.upper())
    return link.endswith(wanted) or f":{wanted}" in link
