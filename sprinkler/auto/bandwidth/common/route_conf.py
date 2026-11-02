"""Parse route.conf for URMA EID planning."""

import re
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class RouteEndpoint:
    role: str
    rank: int
    eid_hex: str
    eid32: str
    suffix4: str

@dataclass(frozen=True)
class RouteConf:
    path: Path
    pair_device_num: int
    local_eids: dict[int, str]
    remote_eids: dict[int, str]

    @classmethod
    def load(cls, path):
        conf_path = Path(path).expanduser()
        return cls.parse(conf_path.read_text(encoding="utf-8", errors="ignore"), conf_path)

    @classmethod
    def parse(cls, text, path=""):
        conf_path = Path(path).expanduser() if path else Path("<memory>")
        pair_device_num = 0
        local_eids = {}
        remote_eids = {}

        for raw_line in str(text or "").splitlines():
            line = cls.clean_value(raw_line)
            if not line or "=" not in line:
                continue
            key, value = [item.strip() for item in line.split("=", 1)]
            value = cls.clean_value(value)
            if key == "pair_device_num":
                if value.isdecimal():
                    pair_device_num = int(value)
                continue

            match = re.fullmatch(r"pair(\d+)_chan0_(local|remote)_eid", key)
            if not match:
                continue
            target = local_eids if match.group(2) == "local" else remote_eids
            target[int(match.group(1))] = cls.clean_hex(value)

        if pair_device_num <= 0:
            keys = set(local_eids) | set(remote_eids)
            pair_device_num = max(keys) + 1 if keys else 0

        return cls(path=conf_path, pair_device_num=pair_device_num, local_eids=local_eids, remote_eids=remote_eids)

    def ranks(self):
        keys = set(self.local_eids) | set(self.remote_eids)
        return sorted(keys) if keys else list(range(self.pair_device_num))

    def host_endpoint(self, rank):
        return self.endpoint("CPU", rank, self.local_eids.get(int(rank), ""))

    def npu_endpoint(self, rank):
        return self.endpoint("NPU", rank, self.remote_eids.get(int(rank), ""))

    def execute_eid(self, rank):
        eid_hex = self.remote_eids.get(int(rank), "")
        return self.format_eid32(eid_hex)

    def endpoint(self, role, rank, eid_hex):
        clean = self.clean_hex(eid_hex)
        return RouteEndpoint(role=role, rank=int(rank), eid_hex=clean, eid32=self.format_eid32(clean),
                             suffix4=self.suffix4(clean))

    @staticmethod
    def clean_value(value):
        text = str(value or "").strip()
        for marker in ("#", ";"):
            if marker in text:
                text = text.split(marker, 1)[0].strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
            text = text[1:-1]
        return text.strip()

    @staticmethod
    def clean_hex(value):
        return re.sub(r"[^0-9a-fA-F]", "", str(value or "")).lower()

    @classmethod
    def format_eid32(cls, value):
        text = cls.clean_hex(value)
        if len(text) < 32:
            return ""
        text = text[-32:]
        return ":".join(text[index : index + 4] for index in range(0, 32, 4))

    @classmethod
    def suffix4(cls, value):
        text = cls.clean_hex(value)
        return text[-4:] if len(text) >= 4 else ""
