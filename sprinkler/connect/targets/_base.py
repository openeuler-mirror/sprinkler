"""Base data classes for connection targets."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, ClassVar


@dataclass(frozen=True)
class ConnectionTarget:
    META_KEYS: ClassVar[set[str]] = {"kind", "sp", "ca", "node", "rank", "cabinet_name", "raw_name", "source",
                                     "asset_category", "endpoint", "subsystem",
                                     "blade_id", "device_type", "cabinet_id"}

    name: str
    host: str
    port: int = 22
    user: str = "root"
    password: str = ""
    meta: Mapping[str, Any] | None = None
    error: str = ""

    def __post_init__(self):
        port, port_error = self._clean_port(self.port)
        object.__setattr__(self, "name", str(self.name or "").strip())
        object.__setattr__(self, "host", str(self.host or "").strip())
        object.__setattr__(self, "port", port)
        object.__setattr__(self, "user", str(self.user or "root").strip() or "root")
        object.__setattr__(self, "password", str(self.password or ""))
        object.__setattr__(self, "meta", self._clean_meta(self.meta))
        object.__setattr__(self, "error", str(self.error or ""))
        if port_error and not self.error:
            object.__setattr__(self, "error", port_error)

    @classmethod
    def from_value(cls, value):
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(
                name=str(value.get("name") or value.get("host") or ""),
                host=str(value.get("host") or "").strip(),
                port=value["port"] if "port" in value else 22,
                user=str(value.get("user") or "root").strip() or "root",
                password=str(value.get("password") or ""),
                meta=value.get("meta"),
                error=str(value.get("error") or ""),
            )
        return cls(name=str(value or "target"), host="", error=f"bad target input: {type(value).__name__}")

    def validate(self):
        if self.error:
            return self.error
        if not self.host:
            return "target host is required"
        if self.port <= 0 or self.port > 65535:
            return f"target port is invalid: {self.port}"
        return ""

    def label(self):
        return self.name or self.host or "target"

    def to_dict(self):
        return {
            "name": self.name, "host": self.host, "port": self.port, "user": self.user,
            "password": self.password, "meta": dict(self.meta or {}), "error": self.error,
        }

    @staticmethod
    def _clean_port(value):
        if isinstance(value, bool) or value is None:
            return 0, f"target port is invalid: {value}"
        if isinstance(value, int):
            return value, ""
        if isinstance(value, float):
            if value.is_integer():
                return int(value), ""
            return 0, f"target port is invalid: {value}"
        text = str(value).strip()
        if re.fullmatch(r"\d+(?:\.0+)?", text):
            return int(float(text)), ""
        return 0, f"target port is invalid: {value}"

    @classmethod
    def _clean_meta(cls, value):
        if not isinstance(value, Mapping):
            return None
        cleaned = {key: value[key] for key in cls.META_KEYS if key in value}
        return MappingProxyType(cleaned) if cleaned else None


@dataclass(frozen=True)
class CabinetRef:
    sp: str
    ca: str
    sheet_name: str
    sheet_index: int


@dataclass(frozen=True)
class NodeRef:
    sp: str
    ca: str
    node: int
    cabinet_name: str


def _build_node_ref(sp: str, cabinet_ref: CabinetRef, node: int) -> NodeRef:
    return NodeRef(sp=sp, ca=cabinet_ref.ca, node=node, cabinet_name=cabinet_ref.sheet_name)


def _build_meta(kind: str, node_ref: NodeRef, raw_name: str = "") -> dict:
    return {
        "kind": kind, "sp": node_ref.sp, "ca": node_ref.ca, "node": node_ref.node,
        "cabinet_name": node_ref.cabinet_name, "raw_name": raw_name, "source": "xlsx",
    }
