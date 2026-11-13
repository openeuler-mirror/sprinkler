"""Target context - unified entry point for target resolution."""

import json
from pathlib import Path
from types import SimpleNamespace

from sprinkler.base.ranges import parse_range
from sprinkler.connect.targets._base import CabinetRef, ConnectionTarget
from sprinkler.connect.targets._builder import TargetBuilder
from sprinkler.connect.targets._ip_spec import NodeIpSpec
from sprinkler.connect.targets._selector import TargetSelector
from sprinkler.utils.utils import env_json_b64


class TargetContext:
    TARGET_SPECS = {
        "cpu_os": ("cpu", "cpu_os"), "cpu_bmc": ("cpu", "cpu_bmc"), "cpu_1213": ("cpu", "cpu_1213"),
        "npu_bmc": ("npu", "npu_bmc"), "npu_1213": ("npu", "npu_1213"), "sfu_bmc": ("sfu", "sfu_bmc"),
        "sfu_com": ("sfu", "sfu_com"), "roce_bmc": ("RoCE", "roce_bmc"), "roce_com": ("RoCE", "roce_com"),
        "smn_os": ("smn", "smn_os"), "smn_bmc": ("smn", "smn_bmc"),
    }

    DIRECT_PORTS = {
        "cpu_os": 22, "cpu_bmc": 22, "cpu_1213": 22,
        "npu_bmc": 22, "npu_1213": 22, "sfu_bmc": 22,
        "sfu_com": 23, "roce_bmc": 22, "roce_com": 23,
        "smn_os": 22, "smn_bmc": 22, "5808_front": 22,
    }

    # 仅支持 -ip 直连的目标类型（暂不解析 xlsx 页签）。
    DIRECT_ONLY_TARGETS = {"5808_front"}

    # NPU OS：目标为 npu<rank>，本机系统 ssh 直连；给 -ip 时经该 CPU OS 跳板。
    NPU_OS_TARGET = "npu_os"

    def __init__(self, args):
        self.args = args
        self.remote_plan = self._load_remote_plan(args)
        self.selector = TargetSelector(args)
        self.builder = TargetBuilder(self.selector.user, "")
        self.node_ip_values = self._node_ip_values(args) if not self.remote_plan else []
        self.direct_values = self._direct_values(args) if not self.remote_plan else []
        if self.node_ip_values and self.direct_values:
            raise ValueError("-node IP格式 与 -ip 不能同时使用，请选择其一")
        self._targets_cache = {}
        self._local_targets = None

    def get_targets(self, target_type: str):
        target_type = self._target_type(target_type)
        if target_type == self.NPU_OS_TARGET:
            return self._npu_os_targets()
        if target_type in self.DIRECT_ONLY_TARGETS:
            return self._direct_only_targets(target_type)
        if target_type not in self.TARGET_SPECS:
            raise ValueError(f"target type {target_type or '<empty>'} is not supported")
        if self.remote_plan:
            return self._remote_targets((target_type,))
        if self.node_ip_values:
            return self._node_ip_targets(target_type)
        if self.direct_values:
            return self._direct_targets(target_type)
        device_type, builder_name = self.TARGET_SPECS[target_type]
        return self._get_targets(target_type, device_type, getattr(self.builder, builder_name))

    def get_targets_by_types(self, target_types):
        target_types = [self._target_type(target_type) for target_type in target_types]
        for target_type in target_types:
            if target_type not in self.TARGET_SPECS:
                raise ValueError(f"target type {target_type or '<empty>'} is not supported")
        if self.remote_plan:
            return self._remote_targets(target_types)
        if self.node_ip_values:
            if len(target_types) != 1:
                raise ValueError("-node IP格式仅支持单个 -target-type")
            return self._node_ip_targets(target_types[0])
        if self.direct_values:
            if len(target_types) != 1:
                raise ValueError("-ip supports one -target-type per run")
            return self._direct_targets(target_types[0])

        targets = []
        for target_type in target_types:
            device_type, builder_name = self.TARGET_SPECS[target_type]
            builder_func = getattr(self.builder, builder_name)
            targets.extend(self._get_targets(target_type, device_type, builder_func, required=False))
        if not targets:
            raise ValueError(f"no targets selected for {','.join(target_types)}")
        return targets

    def get_local_targets(self):
        if self._local_targets is None:
            local = str(getattr(self.args, "local", "") or "").strip()
            if not local:
                raise ValueError("-local requires a value")
            if self.node_ip_values:
                indexes = parse_range(local)
                if not indexes:
                    raise ValueError("-local matched no node-ip targets")
                missing = [i for i in indexes if i < 0 or i >= len(self.node_ip_values)]
                if missing:
                    available = f"0-{len(self.node_ip_values) - 1}"
                    raise ValueError(f"-local index {missing[0]} is outside node-ip target range {available}")
                targets = self._node_ip_targets("cpu_os")
                self._local_targets = [targets[i] for i in indexes]
            elif self.direct_values:
                indexes = parse_range(local)
                if not indexes:
                    raise ValueError(f"-local contains no valid indexes: {local}")
                self._local_targets = [self.direct_values[i] for i in indexes if i < len(self.direct_values)]
            else:
                selector = TargetSelector(self._local_args(local))
                builder = TargetBuilder(selector.user, "")
                self._local_targets = self._build_targets_for(selector, "cpu", builder.cpu_os)
            return list(self._local_targets)

    def _direct_only_targets(self, target_type):
        if not self.direct_values:
            raise ValueError(f"{target_type.replace('_', '-')} 需通过 -ip 指定管理 IP")
        return self._direct_targets(target_type)

    def _npu_os_targets(self):
        ranks = parse_range(str(getattr(self.args, "rank", "") or "0-7"))
        if not ranks:
            raise ValueError("-rank 不能为空")
        if not self.direct_values:
            return [ConnectionTarget(
                name=f"npu{rank}", host=f"npu{rank}", user="root",
                meta={"kind": self.NPU_OS_TARGET, "rank": rank, "source": "local"})
                for rank in ranks]
        return [
            ConnectionTarget(
                name=f"npu{rank}", host=item["host"], port=item["port"] or 22,
                user=item["user"], password=item["password"],
                meta={"kind": self.NPU_OS_TARGET, "rank": rank, "source": "cli_ip"})
            for item in self.direct_values
            for rank in ranks
        ]

    def _node_ip_values(self, args):
        node_text = str(getattr(args, "node", "") or "").strip()
        if not node_text or not NodeIpSpec.is_ip_spec(node_text):
            return []
        ip_text = str(getattr(args, "ip", "") or "").strip()
        if ip_text:
            raise ValueError("-node IP格式 与 -ip 不能同时使用，请选择其一")
        conflicts = [name for name in ("sp", "ca", "xlsx")
                     if str(getattr(args, name, "") or "").strip()]
        if conflicts:
            raise ValueError(f"-node IP格式 与 -{'/-'.join(conflicts)} 不能同时使用")
        default_user = str(getattr(args, "user", "root") or "root")
        default_pwd = str(getattr(args, "pwd", "") or "")
        return NodeIpSpec.parse(node_text, default_user, default_pwd)

    def _node_ip_targets(self, target_type):
        cache_key = f"node_ip_{target_type}"
        if cache_key not in self._targets_cache:
            default_port = self.DIRECT_PORTS.get(target_type, 22)
            self._targets_cache[cache_key] = [
                ConnectionTarget(name=entry.host, host=entry.host,
                    port=entry.port or default_port,
                    user=entry.user, password=entry.password,
                    meta={"kind": target_type, "node": idx, "source": "cli_node_ip"})
                for idx, entry in enumerate(self.node_ip_values)]
        return list(self._targets_cache[cache_key])

    def _direct_targets(self, target_type):
        cache_key = f"direct_{target_type}"
        if cache_key not in self._targets_cache:
            default_port = self.DIRECT_PORTS.get(target_type, 22)
            self._targets_cache[cache_key] = [ConnectionTarget(name=item["endpoint"], host=item["host"],
                port=item["port"] or default_port,
                user=item["user"], password=item["password"],
                meta={"kind": target_type, "node": index, "source": "cli_ip"})
                for index, item in enumerate(self.direct_values)]
        return list(self._targets_cache[cache_key])

    def _direct_values(self, args):
        ip_text = str(getattr(args, "ip", "") or "").strip()
        user_text = str(getattr(args, "user", "root") or "root")
        pwd_text = str(getattr(args, "pwd", "") or "")
        if not ip_text:
            if pwd_text:
                raise ValueError("-pwd requires -ip")
            if "," in user_text:
                raise ValueError("multiple -user values require -ip")
            return []

        conflicts = [name for name in ("sp", "ca", "node", "xlsx")
                     if str(getattr(args, name, "") or "").strip()]
        if conflicts:
            raise ValueError(f"use either -ip or -{'/-'.join(conflicts)}, not both")

        entries = self._csv_values(ip_text, "-ip")
        profile_entries = [len(item.split()) > 1 for item in entries]
        if any(profile_entries) and not all(profile_entries):
            raise ValueError("-ip cannot mix endpoint-only and endpoint/user/password entries")
        if all(profile_entries):
            if pwd_text or user_text != "root":
                raise ValueError("-ip includes credentials; do not use -user/-pwd")
            return self._profile_values(entries)

        users = self._broadcast_values(self._csv_values(user_text, "-user"), len(entries), "-user")
        passwords = ([""] * len(entries) if not pwd_text else
                     self._broadcast_values(self._csv_values(pwd_text, "-pwd"), len(entries), "-pwd"))
        values = []
        for endpoint, user, password in zip(entries, users, passwords):
            host, port = self._endpoint(endpoint, "-ip")
            values.append({"endpoint": endpoint, "host": host, "port": port,
                           "user": user, "password": password})
        return values

    def _profile_values(self, entries):
        values = []
        for item in entries:
            parts = item.split()
            if len(parts) != 3:
                raise ValueError("each credentialed -ip entry must be: IP[:port] user password")
            endpoint, user, password = parts
            host, port = self._endpoint(endpoint, "-ip")
            values.append({"endpoint": endpoint, "host": host, "port": port,
                           "user": user, "password": password})
        return values

    @staticmethod
    def _endpoint(value, option):
        host, separator, port_text = value.rpartition(":")
        if not separator:
            return value, None
        if not host or not port_text.isdigit():
            raise ValueError(f"{option} endpoint must be IP or IP:port: {value}")
        port = int(port_text)
        if port < 1 or port > 65535:
            raise ValueError(f"{option} port must be 1-65535: {port_text}")
        return host, port

    @staticmethod
    def _csv_values(value, option):
        values = [item.strip() for item in str(value or "").split(",")]
        if not values or any(not item for item in values):
            raise ValueError(f"{option} contains an empty value")
        return values

    @staticmethod
    def _broadcast_values(values, count, option):
        if len(values) == 1:
            return values * count
        if len(values) != count:
            raise ValueError(f"{option} count must be 1 or match -ip count {count}, got {len(values)}")
        return values

    def _get_targets(self, target_type: str, device_type: str, builder_func, required=True):
        cache_key = f"{target_type}_{device_type}"
        if cache_key not in self._targets_cache:
            targets = self._build_targets_for(self.selector, device_type, builder_func, required=required)
            self._targets_cache[cache_key] = targets
        if required and not self._targets_cache[cache_key]:
            raise ValueError(f"no {device_type} targets selected")
        return list(self._targets_cache[cache_key])

    def _build_targets_for(self, selector, device_type: str, builder_func, required=True):
        targets = []
        for sp, path in selector.superpod_files():
            for cabinet_ref in selector.cabinet_refs(sp, path):
                cabinet = selector.load_cabinet_data(cabinet_ref, path)
                devices = {item["index"]: item for item in cabinet.get(device_type, [])}
                if not devices:
                    continue
                for node in selector.selected_nodes(cabinet_ref, devices, device_type):
                    targets.append(builder_func(sp, cabinet_ref, node, devices[node]))
        if required and not targets:
            raise ValueError(f"no {device_type} targets selected")
        return targets

    def _remote_targets(self, target_types):
        if "remote_targets" not in self._targets_cache:
            self._targets_cache["remote_targets"] = [ConnectionTarget.from_value(item)
                                                     for item in self.remote_plan.get("targets", [])]
            if not self._targets_cache["remote_targets"]:
                raise ValueError("remote plan has no targets")

        wanted = set(target_types or [])
        targets = []
        for target in self._targets_cache["remote_targets"]:
            target_type = str((target.meta or {}).get("kind") or "cpu_os")
            if target_type in wanted:
                targets.append(target)
        if not targets:
            raise ValueError(f"remote plan has no targets for {','.join(target_types)}")
        return targets

    def _local_args(self, local):
        values = dict(vars(self.args))
        current_node = str(getattr(self.args, "node", "") or "").strip()
        local_node = str(local or "").strip()
        values["node"] = (TargetSelector.structured_node_with_nodes(current_node, local_node)
                          if current_node.startswith("{") and not local_node.startswith("{") else local_node)
        if str(values["node"] or "").strip().startswith("{"):
            values["sp"] = None
            values["ca"] = None
        return SimpleNamespace(**values)

    def _load_remote_plan(self, args):
        plan = env_json_b64("SPRINKLER_REMOTE_PLAN_B64")
        if plan:
            return plan
        path = str(getattr(args, "remote_plan", "") or "").strip()
        if not path:
            return {}
        plan_path = Path(path).expanduser()
        data = json.loads(plan_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"bad remote plan: {plan_path}")
        return data

    def _target_type(self, target_type):
        return str(target_type or "").strip().replace("-", "_").lower()
