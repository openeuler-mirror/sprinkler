"""前台交换机（switch）目标收集 —— checker 目录内的独立收集器。

前台交换机统一为 ``switch`` 目标类型后，其目标来源与既有 connect 模块的
``TargetContext`` 无关（connect 的目标收集按 cpu/npu/sfu 设备族划分，无法
直接表达"从 CPU/NPU 取前台 IP、从 SFU 取 COM IP"这类跨设备族规则），因此
本模块在 checker 目录内自行收集，只读复用 connect 的 ``TargetSelector`` /
``ConnectionTarget``，不改动 connect 目录的任何代码

目标来源（取首个命中的来源，优先级从上到下）：

1. **Remote plan**（SPRINKLER_REMOTE_PLAN_B64 / -remote-plan）：取
   ``meta.kind == "switch"`` 的目标。
2. **-ip 直连**：``-ip <host>[:port][,<host2>...]`` + ``-user`` / ``-pwd``，
   直接作为前台交换机管理地址（SSH 22 直连）。
3. **Excel 机柜数据**（-sp / -ca / -node / -xlsx）：从每个 cabinet 的
   CPU / NPU 条目取 ``frontend_host``（1213 前台 IP）、SFU 条目取
   ``com_host``（1260 COM），非空者作为 switch 目标，索引连续编号；
   ``-node`` 过滤作用在这个连续编号上。

凭据约定：默认用户 ``huawei``（交换机管理账号），密码优先取 ``-pwd``，
其次取密码库 ``huawei`` 类别默认值；``-user`` 显式指定时尊重用户配置
（仅当值为默认 root 时覆盖为 huawei）
"""

from sprinkler.connect.targets import ConnectionTarget, TargetSelector
from sprinkler.utils.utils import env_json_b64

# 前台交换机默认账号：huawei（与 NPU_Checker 直连实践一致）。
SWITCH_USER = "huawei"

# SSH 直连默认端口。
SWITCH_PORT = 22


def collect_switch_targets(args) -> list:
    """收集前台交换机目标，返回 ``ConnectionTarget`` 列表。

    按 目标来源 的优先级分派：先 remote plan，再 -ip，最后 Excel。
    来源解析异常时抛错由调用方（策略 / dry-run）决定降级或告警。
    """
    remote = _remote_switch_targets(args)
    if remote:
        return remote
    direct = _ip_switch_targets(args)
    if direct:
        return direct
    return _xlsx_switch_targets(args)


def list_switch_targets(args) -> list:
    """兼容 TargetContext 接口的别名：返回收集到的目标列表。"""
    return collect_switch_targets(args)


def _remote_switch_targets(args) -> list:
    plan = _load_remote_plan(args)
    if not plan:
        return []
    targets = [ConnectionTarget.from_value(item) for item in plan.get("targets", [])]
    return [t for t in targets if str((t.meta or {}).get("kind") or "") == "switch"]


def _load_remote_plan(args) -> dict:
    plan = env_json_b64("SPRINKLER_REMOTE_PLAN_B64")
    if plan:
        return plan
    path = str(getattr(args, "remote_plan", "") or "").strip()
    if not path:
        return {}
    import json
    from pathlib import Path

    plan_path = Path(path).expanduser()
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _ip_switch_targets(args) -> list:
    text = str(getattr(args, "ip", "") or "").strip()
    if not text:
        return []
    user = _switch_user(args)
    pwd = _switch_password(args)
    targets = []
    for index, token in enumerate(text.split(",")):
        token = token.strip()
        if not token:
            continue
        host, port = _split_endpoint(token)
        if not host:
            continue
        targets.append(ConnectionTarget(
            name=f"switch{index}",
            host=host,
            port=port,
            user=user,
            password=pwd,
            meta={"kind": "switch", "node": index, "source": "cli_ip"},
        ))
    return targets


def _xlsx_switch_targets(args) -> list:
    selector = TargetSelector(args)
    targets = []
    for sp, path in selector.superpod_files():
        for cabinet_ref in selector.cabinet_refs(sp, path):
            cabinet = selector.load_cabinet_data(cabinet_ref, path)
            devices = _collect_switch_devices(cabinet)
            if not devices:
                continue
            selected = _selected_switch_nodes(selector, cabinet_ref, devices, args)
            for index in selected:
                host, kind, raw_name = devices[index]
                targets.append(_build_switch_target(
                    sp, cabinet_ref, index, host, kind, raw_name,
                    user=_switch_user(args), password=_switch_password(args),
                ))
    return targets


def _collect_switch_devices(cabinet: dict) -> list:
    """把 cabinet 数据中的前台交换机地址收集为有序列表。

    返回 ``[(host, kind, raw_name), ...]``，索引即连续编号（供 -node 过滤）。
    - CPU / NPU：``frontend_host``（1213 前台 IP）。
    - SFU：``com_host``（1260 COM）。
    """
    devices = []
    for kind in ("cpu", "npu"):
        for item in cabinet.get(kind, []) or []:
            host = str(item.get("frontend_host") or "").strip()
            if host:
                devices.append((host, kind, str(item.get("raw_name") or "")))
    for item in cabinet.get("sfu", []) or []:
        host = str(item.get("com_host") or "").strip()
        if host:
            devices.append((host, "sfu", str(item.get("raw_name") or "")))
    for item in cabinet.get("RoCE", []) or []:
        host = str(item.get("frontend_host") or "").strip()
        if host:
            devices.append((host, "RoCE", str(item.get("raw_name") or "")))
    return devices


def _selected_switch_nodes(selector, cabinet_ref, devices: list, args) -> list:
    """应用 -node 过滤（作用于连续索引）；未给出 -node 时选全部。

    复用 ``TargetSelector.selected_nodes`` 的解析与校验语义：传入的设备
    字典 key 为连续索引，label 用 "switch" 便于错误提示。
    """
    device_map = {index: {"host": host} for index, (host, _, _) in enumerate(devices)}
    if _node_specified(args):
        return selector.selected_nodes(cabinet_ref, device_map, "switch")
    return sorted(device_map)


def _node_specified(args) -> bool:
    return bool(str(getattr(args, "node", "") or "").strip())


def _build_switch_target(sp, cabinet_ref, index, host, kind, raw_name,
                         user, password) -> ConnectionTarget:
    return ConnectionTarget(
        name=f"s{sp}:c{cabinet_ref.ca}:n{index}_switch",
        host=host,
        port=SWITCH_PORT,
        user=user,
        password=password,
        meta={
            "kind": "switch",
            "sp": sp,
            "ca": cabinet_ref.ca,
            "node": index,
            "cabinet_name": cabinet_ref.sheet_name,
            "raw_name": raw_name,
            "source": "xlsx",
        },
    )


def _switch_user(args) -> str:
    user = str(getattr(args, "user", "") or "").strip()
    if not user or user == "root":
        return SWITCH_USER
    return user


def _switch_password(args) -> str:
    pwd = str(getattr(args, "pwd", "") or "").strip()
    if pwd:
        return pwd
    return _default_switch_password()


def _default_switch_password() -> str:
    try:
        from sprinkler.utils.password_store import get_default_passwords
        passwords = get_default_passwords("huawei")
        if passwords:
            return passwords[0]
    except Exception:
        pass
    return ""


def _split_endpoint(token: str):
    host, separator, port_text = token.rpartition(":")
    if not separator or not host or not port_text.isdigit():
        return token, SWITCH_PORT
    port = int(port_text)
    if port < 1 or port > 65535:
        raise ValueError(f"switch -IP endpoint port must be 1-65535: {port_text}")
    return host, port
