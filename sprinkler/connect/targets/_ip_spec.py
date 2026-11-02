"""IP specification parser for -node IP format."""

import re
from dataclasses import dataclass


class NodeIpSpec:
    _IP_PATTERN = re.compile(r"\d+\.\d+\.\d+\.\d+")

    @dataclass(frozen=True)
    class Entry:
        host: str
        port: int | None = None
        user: str = ""
        password: str = ""

    @staticmethod
    def is_ip_spec(value: str) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        if ";" in text:
            return True
        return bool(NodeIpSpec._IP_PATTERN.fullmatch(text.split(",")[0].rsplit(":", 1)[0].strip()))

    @staticmethod
    def split_endpoint(value: str) -> tuple[str, int | None]:
        host, sep, port_text = value.rpartition(":")
        if sep and port_text.isdigit():
            port = int(port_text)
            if port < 1 or port > 65535:
                raise ValueError(f"-node 端口范围1-65535: {port_text}")
            return host, port
        return value, None

    @staticmethod
    def broadcast_values(values: list[str], count: int, default: str, label: str, spec: str) -> list[str]:
        if not values:
            return [default] * count
        if len(values) == 1:
            return values * count
        if len(values) != count:
            raise ValueError(f"-node {label}数量({len(values)})须为1或等于IP数量({count}): {spec}")
        return values

    @staticmethod
    def parse_optional_segment(parts: list[str], index: int, label: str) -> list[str]:
        if index >= len(parts):
            return []
        segment = parts[index].strip()
        if not segment:
            return []
        values = [s.strip() for s in segment.split(",")]
        if not values or all(v == "" for v in values):
            return []
        if any(v == "" for v in values):
            raise ValueError(f"-node {label}列表包含空值: '{parts[index]}'")
        return values

    @staticmethod
    def parse(spec: str, default_user: str = "root", default_pwd: str = "") -> list["NodeIpSpec.Entry"]:
        parts = spec.split(";")
        if len(parts) > 3:
            raise ValueError(f"-node IP规格最多3段(IP;user;pwd), 实际{len(parts)}段: {spec}")
        ip_list = [s.strip() for s in parts[0].split(",") if s.strip()]
        if not ip_list:
            raise ValueError(f"-node IP规格缺少IP地址: {spec}")
        for item in ip_list:
            host = item.rsplit(":", 1)[0] if ":" in item and item.rsplit(":", 1)[1].isdigit() else item
            if not NodeIpSpec._IP_PATTERN.fullmatch(host):
                raise ValueError(f"-node IP格式错误: '{item}', 期望 x.x.x.x 或 x.x.x.x:port")
        user_list = NodeIpSpec.parse_optional_segment(parts, 1, "用户名")
        pwd_list = NodeIpSpec.parse_optional_segment(parts, 2, "密码")
        users = NodeIpSpec.broadcast_values(user_list, len(ip_list), default_user, "用户名", spec)
        pwds = NodeIpSpec.broadcast_values(pwd_list, len(ip_list), default_pwd, "密码", spec)
        entries=  []
        for ip_item, user, pwd in zip(ip_list, users, pwds):
            host, port = NodeIpSpec.split_endpoint(ip_item)
            entries.append(NodeIpSpec.Entry(host=host, port=port, user=user, password=pwd))
        return entries
