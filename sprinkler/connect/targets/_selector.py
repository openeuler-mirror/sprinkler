"""Target selector for parsing -sp/-ca/-node arguments."""

import contextlib
import io
import re
from pathlib import Path

from sprinkler.base.ranges import parse_hex_range, parse_range
from sprinkler.connect.targets._base import CabinetRef
from sprinkler.connect.targets._ip_spec import NodeIpSpec
from sprinkler.utils.utils import default_config_dir


class TargetSelector:
    DEFAULT_STRUCTURED_NODE_RANGE = "0-7"

    def __init__(self, args):
        self.sp = getattr(args, "sp", None)
        self.ca = getattr(args, "ca", None)
        self.node = getattr(args, "node", None)
        self.xlsx = getattr(args, "xlsx", None)
        self.user = getattr(args, "user", "root")
        node_text = str(self.node or "").strip()
        self.structured_nodes = {} if NodeIpSpec.is_ip_spec(node_text) else self._parse_structured_nodes(self.node)
        self.nodes_by_cabinet = {}

    def superpod_files(self):
        requested = self._sp_values(self.sp)
        if self.xlsx:
            return self._explicit_superpod_files(self.xlsx, requested)

        config_dir = default_config_dir()
        if not requested and self.structured_nodes:
            requested = sorted(self.structured_nodes)

        if requested:
            files = [(sp, config_dir / f"superpod_{sp}.xlsx") for sp in requested]
            matched = [(sp, path) for sp, path in files if path.exists()]
            if len(matched) != len(files):
                missing = ",".join(sp for sp, path in files if not path.exists())
                raise FileNotFoundError(f"no superpod xlsx matched -sp {self.sp or missing} in {config_dir}")
            return matched

        files = []
        for path in sorted(config_dir.glob("superpod_*.xlsx")):
            sp = self._sp_from_path(path)
            if sp:
                files.append((sp, path))
        if not files:
            raise FileNotFoundError(f"no superpod xlsx found in {config_dir}")
        return files

    def cabinet_refs(self, sp: str, path):
        sheets = self._sheet_names(path)
        if self.structured_nodes:
            refs = []
            for ca_token, nodes in self.structured_nodes.get(sp, []):
                ref = self._cabinet_ref(sp, sheets, ca_token)
                refs.append(ref)
                key = (sp, ref.sheet_index)
                self.nodes_by_cabinet[key] = list(dict.fromkeys([*self.nodes_by_cabinet.get(key, []), *nodes]))
            return self._unique_refs(refs)

        if self.ca:
            refs = [self._cabinet_ref(sp, sheets, token) for token in self._ca_tokens(self.ca)]
            return self._unique_refs(refs)

        return [CabinetRef(sp=sp, ca=self._ca_label(index, name), sheet_name=name, sheet_index=index)
                for index, name in enumerate(sheets)]

    def selected_nodes(self, cabinet_ref: CabinetRef, devices: dict[int, dict], label="node"):
        selected = self.nodes_by_cabinet.get((cabinet_ref.sp, cabinet_ref.sheet_index))
        if selected is None and self.node and not self.structured_nodes:
            selected = parse_range(self.node)
        if selected is None:
            selected = sorted(devices)
        if not selected:
            raise ValueError(f"-node matched no {label} nodes")

        missing = [node for node in selected if node not in devices]
        if missing:
            available = ",".join(str(index) for index in sorted(devices))
            raise ValueError(f"no {label} node {missing[0]} in sp={cabinet_ref.sp} "
                             f"ca={cabinet_ref.sheet_name}; available={available or 'none'}")
        return list(selected)

    def load_cabinet_data(self, ref: CabinetRef, path):
        from sprinkler.config.superpod_info import read_cabinet as read_superpod_cabinet

        with contextlib.redirect_stdout(io.StringIO()):
            return read_superpod_cabinet(ref.sp, ref.sheet_name, default_user=self.user,
                                         default_password="", file_path=path)

    def _explicit_superpod_files(self, spec, requested: list[str]):
        path = Path(str(spec)).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"-xlsx path not found: {path}")
        if path.is_dir():
            files = self._config_dir_superpod_files(path, requested)
            if files:
                return files
            selector = ",".join(requested) if requested else "all"
            raise FileNotFoundError(f"no superpod xlsx matched -sp {selector} in {path}")
        if path.suffix.lower() not in {".xlsx", ".xlsm", ".xls"}:
            raise ValueError(f"-xlsx expects an Excel file or config directory: {path}")

        sp = self._sp_from_path(path)
        if not sp:
            if len(requested) == 1:
                sp = requested[0]
            else:
                raise ValueError(f"-xlsx file name must be superpod_<sp>.xlsx or use -sp <id>: {path}")
        if requested and sp not in requested:
            raise ValueError(f"-xlsx {path} is superpod {sp}, but -sp selected {','.join(requested)}")
        return [(sp, path)]

    def _config_dir_superpod_files(self, config_dir: Path, requested: list[str]):
        if requested:
            return [(sp, path) for sp in requested for path in [config_dir / f"superpod_{sp}.xlsx"] if path.exists()]
        files = []
        for path in sorted(config_dir.glob("superpod_*.xlsx")):
            sp = self._sp_from_path(path)
            if sp:
                files.append((sp, path))
        return files

    def _sheet_names(self, path):
        import pandas as pd

        xls = pd.ExcelFile(path)
        try:
            return list(xls.sheet_names)
        finally:
            xls.close()

    def _sp_values(self, spec):
        if not str(spec or "").strip():
            return []
        return [format(value, "x") for value in parse_hex_range(str(spec))]

    def _sp_from_path(self, path: Path):
        match = re.fullmatch(r"superpod_(.+)\.xls[xm]?", path.name, re.IGNORECASE)
        return match.group(1).lower() if match else ""

    def _parse_structured_nodes(self, spec):
        text = str(spec or "").replace(" ", "")
        if not text.startswith("{"):
            return {}
        if not text.endswith("}"):
            raise ValueError(f"structured -node must be quoted: -node \"{spec}\"")
        result: dict[str, list[tuple[str, list[int]]]] = {}
        for match in re.finditer(r"s([^:\[\]\{\},]+):\[(.*?)\]", text):
            sp = format(parse_hex_range(match.group(1))[0], "x")
            items = []
            for entry in self._split_csv(match.group(2)):
                if ":" not in entry:
                    items.extend(self._structured_cabinet_shorthand(entry))
                    continue
                ca_token, node_spec = entry.rsplit(":", 1)
                items.append((ca_token, self._node_values(node_spec)))
            result[sp] = items
        if not result:
            raise ValueError(f"bad structured -node selector: {spec}")
        return result

    def _structured_cabinet_shorthand(self, entry):
        if not re.fullmatch(r"c[0-9a-fA-F]+", str(entry or "")):
            raise ValueError(f"bad structured -node entry: {entry}")
        return [(entry, self._node_values(self.DEFAULT_STRUCTURED_NODE_RANGE))]

    def _node_values(self, spec):
        nodes = self._parse_node_numbers(spec)
        if not nodes:
            raise ValueError(f"invalid node spec: {spec}")
        return nodes

    def _parse_node_numbers(self, spec: str):
        text = str(spec or "").replace(" ", "")
        if not text:
            raise ValueError(f"invalid node spec: {spec}")
        out = []
        prev_digit = None
        dash_pending = False
        for ch in text:
            if ch.isdigit():
                current = int(ch)
                if dash_pending:
                    if prev_digit is None:
                        raise ValueError(f"invalid node spec: {spec}")
                    step = 1 if prev_digit <= current else -1
                    out.extend(range(prev_digit, current + step, step))
                    prev_digit = None
                    dash_pending = False
                else:
                    out.append(current)
                    prev_digit = current
            elif ch == "-":
                dash_pending = True
            else:
                raise ValueError(f"invalid node spec: {spec}")
        if dash_pending:
            raise ValueError(f"invalid node spec: {spec}")
        if not out:
            raise ValueError(f"invalid node spec: {spec}")
        return list(dict.fromkeys(out))

    def _ca_tokens(self, spec):
        tokens = []
        for token in self._split_csv(str(spec or "").replace(" ", "")):
            if self._is_ca_index_range(token):
                tokens.extend(format(value, "x") for value in parse_hex_range(token))
            else:
                tokens.append(token)
        return tokens

    def _cabinet_ref(self, sp: str, sheets: list[str], token: str):
        text = str(token).strip()
        if text in sheets:
            index = sheets.index(text)
            return CabinetRef(sp=sp, ca=self._ca_label(index, text), sheet_name=text, sheet_index=index)
        if re.fullmatch(r"c[0-9a-fA-F]+", text):
            text = text[1:]
        if self._is_ca_index(text):
            index = parse_hex_range(text)[0]
            if index < 0 or index >= len(sheets):
                available = f"0-{format(max(len(sheets) - 1, 0), 'x')}"
                raise ValueError(f"no sheet index {text} in sp={sp}; available={available}")
            return CabinetRef(sp=sp, ca=self._ca_label(index, sheets[index]), sheet_name=sheets[index],
                              sheet_index=index)
        raise ValueError(f"no sheet matched -ca {text} in sp={sp}")

    def _ca_label(self, index, sheet_name=""):
        if str(sheet_name).strip() == "5808":
            return "5808"
        if int(index) >= 0:
            return format(int(index), "x")
        return str(index)

    def _unique_refs(self, refs: list[CabinetRef]):
        seen = set()
        result = []
        for ref in refs:
            key = (ref.sp, ref.sheet_index)
            if key not in seen:
                seen.add(key)
                result.append(ref)
        return result

    @staticmethod
    def _is_ca_index(token: str):
        return bool(re.fullmatch(r"[0-9a-fA-F]+", str(token or "")))

    @staticmethod
    def _is_ca_index_range(token: str):
        text = str(token or "")
        if not re.fullmatch(r"[0-9a-fA-F]+-[0-9a-fA-F]+", text):
            return False
        left, right = text.split("-", 1)
        return len(left) <= 2 and len(right) <= 2

    @staticmethod
    def _split_csv(text: str):
        return [item for item in str(text or "").split(",") if item]

    @classmethod
    def structured_node_with_nodes(cls, spec, node_spec):
        text = str(spec or "").replace(" ", "")
        groups = []
        for match in re.finditer(r"s([^:\[\]\{\},]+):\[(.*?)\]", text):
            entries = []
            for entry in cls._split_csv(match.group(2)):
                ca_token = entry.rsplit(":", 1)[0] if ":" in entry else entry
                entries.append(f"{ca_token}:{node_spec}")
            groups.append(f"s{match.group(1)}:[{','.join(entries)}]")
        return "{" + ",".join(groups) + "}" if groups else node_spec
