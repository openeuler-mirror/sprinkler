"""BIOS 高性能配置的只读采集与白名单比对。

流程与 ``bios_whitelist_tool`` 原型一致，但复用项目已有的
:class:`sprinkler.checker.version.query.channels.redfish.RedfishReader`：

1. ``GET /redfish/v1/Systems/{id}/Bios`` 取当前 ``Attributes`` 与注册表名；
2. ``GET /redfish/v1/Registries`` 按版本匹配 ``BiosAttributeRegistry``；
3. 读取注册表（只保留白名单命中的属性），得到展示名与菜单路径；
4. 按白名单别名匹配，输出当前值与 BMC 菜单路径。

本模块**只读也只展示**：不调用 PATCH，不修改 BIOS，不重启服务器，也不做
期望值比对。任何调整都由用户按菜单路径自行在 BMC 页面完成。
"""

import json
import re
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import quote, urlparse

try:
    import requests
except ImportError:  # pragma: no cover - 依赖缺失时在运行期给出明确提示
    requests = None

from sprinkler.checker.bios.whitelist import Whitelist
from sprinkler.checker.version.query.channels.redfish import RedfishReader

REGISTRY_MARKER = "BiosAttributeRegistry"
PATH_SEPARATOR = "->"


class BiosInspectError(RuntimeError):
    """BIOS 采集或注册表解析失败。"""


def format_menu_path(path: str, leaf: str = "") -> str:
    """把注册表的 ``./Advanced/Socket Configuration/...`` 统一为 ``A->B->C``。

    BIOS 菜单名里可能自带斜杠（如 ``I/O``），因此先做占位保护再按路径分隔符
    切分，避免把 ``Directed I/O`` 拆成 ``Directed->I->O``。

    :param path: 注册表 ``MenuPath`` 或白名单里的 ``menu_path``，分隔符可为 ``/`` 或 ``\\``
    :param leaf: 追加在末尾的展示名（通常是 ``DisplayName``）
    """
    protected = {"I/O": "I\x00O"}
    text = str(path or "")
    for raw, token in protected.items():
        text = text.replace(raw, token)
    parts = [
        part.strip()
        for part in re.split(r"[\\/]+", text)
        if part.strip() and part.strip() != "."
    ]
    if leaf and str(leaf).strip():
        parts.append(str(leaf).strip())
    result = PATH_SEPARATOR.join(parts)
    for raw, token in protected.items():
        result = result.replace(token, raw)
    return result


def normalize_registry_version(value: str) -> Optional[Tuple[int, ...]]:
    """从 ``BiosAttributeRegistry.1.5.3`` / ``BiosAttributeRegistry.v1_5_3`` 提取版本号。"""
    match = re.search(
        r"BiosAttributeRegistry[._]v?([0-9]+(?:[._][0-9]+)*)",
        str(value or ""),
        re.IGNORECASE,
    )
    if not match:
        return None
    return tuple(int(part) for part in re.split(r"[._]", match.group(1)))


def build_reader(host: str, username: str, password: str, timeout: int = 20,
                 port: int = 443) -> RedfishReader:
    """为 BMC 创建已认证的 RedfishReader（关闭证书校验，且忽略系统代理）。"""
    if requests is None:
        raise BiosInspectError("缺少 requests 依赖，请执行: pip install requests")
    if not host:
        raise BiosInspectError("BMC host 为空")
    session = requests.Session()
    session.auth = (username or "Administrator", password or "")
    session.verify = False  # BMC 使用自签名证书，管理网内直连
    session.trust_env = False  # 管理网直连，不使用 HTTP(S)_PROXY
    session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
    if host.startswith(("http://", "https://")):
        base_url = host
    elif port and int(port) != 443:
        base_url = f"https://{host}:{int(port)}"
    else:
        base_url = f"https://{host}"
    return RedfishReader(session, base_url, timeout)


def _describe_status(status: int, resource: str) -> str:
    if status == 401:
        return f"BMC 认证失败（HTTP 401），请检查用户名/密码: {resource}"
    if status == 403:
        return f"BMC 拒绝访问（HTTP 403），请检查账号 Redfish 权限: {resource}"
    if status == 404:
        return f"Redfish 资源不存在（HTTP 404）: {resource}"
    if status == RedfishReader.STATUS_TIMEOUT:
        return f"BMC 请求超时: {resource}"
    if status == RedfishReader.STATUS_CONNECT:
        return f"无法连接 BMC: {resource}"
    if status == RedfishReader.STATUS_TLS:
        return f"BMC TLS 握手失败: {resource}"
    return f"Redfish 请求失败（状态 {status}）: {resource}"


def _stringify(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " | ".join(_stringify(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def build_rows(
    whitelist: Whitelist,
    current: Dict[str, object],
    registry_meta: Dict[str, dict],
) -> Tuple[List[dict], List[str]]:
    """按白名单生成展示行；返回 ``(rows, missing)``。

    ``missing`` 记录当前 BIOS 未暴露的逻辑项，仅提示机型差异，不算异常；
    未暴露项不进入 ``rows``。本函数只做展示，不做期望值比对。
    """
    current_lower = {str(key).lower(): key for key in current}
    rows: List[dict] = []
    missing: List[str] = []
    for category in whitelist.categories:
        for item in category.items:
            matched_key = next(
                (current_lower[alias.lower()] for alias in item.aliases
                 if alias.lower() in current_lower),
                None,
            )
            if matched_key is None:
                missing.append(item.name)
                continue

            value = current[matched_key]
            meta = registry_meta.get(matched_key) or {}
            display_name = meta.get("display_name") or matched_key
            menu_raw = meta.get("menu_path") or item.menu_path
            rows.append({
                "Category": category.name,
                "Attribute": item.name,
                "ZhName": item.zh_name,
                "MatchedAttribute": matched_key,
                "CurrentValue": _stringify(value),
                "FullMenuPath": format_menu_path(menu_raw, display_name),
            })
    return rows, missing


class BiosInspector:
    """单个 CPU BMC 的 BIOS 采集器（只读）。"""

    def __init__(self, reader: RedfishReader, system_id: str = "1",
                 base_url: str = "", username: str = ""):
        self.reader = reader
        self.system_id = str(system_id or "1")
        self.base_url = str(base_url or "").rstrip("/")
        self.username = str(username or "Administrator")
        self.current: Dict[str, object] = {}
        self.registry_name = ""
        self.registry_meta: Dict[str, dict] = {}
        self.warnings: List[str] = []
        self.requests: List[dict] = []

    def _build_curl(self, method: str, uri: str) -> str:
        """按 reader 的会话配置拼一条可直接复制的 curl（密码脱敏）。

        格式固定为::

            curl --noproxy '*' -sk -u "user:*****" "URL" | python3 -m json.tool
        """
        full_url = uri
        if self.base_url and not uri.startswith(("http://", "https://")):
            full_url = f"{self.base_url}{uri}"
        parts = ["curl", "--noproxy", "'*'", "-sk"]
        if method != "GET":
            parts.extend(["-X", method])
        parts.extend(["-u", f'"{self.username}:*****"', f'"{full_url}"'])
        parts.extend(["|", "python3", "-m", "json.tool"])
        return " ".join(parts)

    def _get(self, uri: str) -> Tuple[Optional[dict], int]:
        """支持注册表返回绝对 URL 或相对路径两种情况，并记录 curl 命令轨迹。"""
        text = str(uri or "")
        target = text
        if text.startswith(("http://", "https://")):
            parsed = urlparse(text)
            target = parsed.path or "/"
            if parsed.query:
                target = f"{target}?{parsed.query}"
        data, status = self.reader.get(target)
        curl_cmd = self._build_curl("GET", target)
        self.requests.append({"method": "GET", "uri": target, "curl": curl_cmd,
                              "status": status})
        return data, status

    def load(self, wanted: Optional[Iterable[str]] = None) -> None:
        """读取 BIOS 当前值与属性注册表；注册表失败仅告警，不中断巡检。

        :param wanted: 需要解析的属性名（白名单别名，大小写不敏感）。``None``
            表示解析整张注册表；空集合表示无需解析注册表。
        """
        self.requests = []
        bios_uri = f"/redfish/v1/Systems/{quote(self.system_id, safe='')}/Bios"
        bios, status = self._get(bios_uri)
        if bios is None:
            raise BiosInspectError(_describe_status(status, bios_uri))
        current = bios.get("Attributes")
        if not isinstance(current, dict):
            raise BiosInspectError(f"BIOS 响应缺少 Attributes 对象: {bios_uri}")
        self.current = current
        self.registry_name = str(bios.get("AttributeRegistry") or "")

        wanted_lower = None if wanted is None else {str(name).lower() for name in wanted}
        if wanted_lower is not None and not wanted_lower:
            return
        if not self.registry_name:
            self.warnings.append("BIOS 响应缺少 AttributeRegistry，菜单路径可能缺失")
            return
        try:
            self.registry_meta = self._load_registry(self.registry_name, wanted_lower)
        except BiosInspectError as exc:
            self.warnings.append(f"读取 BIOS 注册表失败，菜单路径可能缺失: {exc}")

    def _load_registry(self, registry_name: str,
                       wanted: Optional[Set[str]] = None) -> Dict[str, dict]:
        """解析注册表；``wanted`` 非空时只保留命中的属性，避免缓存整表。"""
        registries, status = self._get("/redfish/v1/Registries")
        if registries is None:
            raise BiosInspectError(_describe_status(status, "/redfish/v1/Registries"))
        members = registries.get("Members") or []
        target_version = normalize_registry_version(registry_name)

        registry_uri = ""
        candidates = []
        for member in members:
            uri = str((member or {}).get("@odata.id") or "")
            if REGISTRY_MARKER not in uri:
                continue
            candidates.append(uri)
            if target_version and normalize_registry_version(uri) == target_version:
                registry_uri = uri
                break
        if not registry_uri and target_version is None and candidates:
            registry_uri = candidates[0]
        if not registry_uri:
            raise BiosInspectError(
                f"未匹配注册表 {registry_name}；候选: {', '.join(candidates) or '无'}"
            )

        registry_info, status = self._get(registry_uri)
        if registry_info is None:
            raise BiosInspectError(_describe_status(status, registry_uri))
        locations = registry_info.get("Location") or []
        if not locations:
            raise BiosInspectError(f"注册表元数据缺少 Location: {registry_uri}")
        location = next(
            (item for item in locations
             if str((item or {}).get("Language") or "").lower().startswith("en")),
            locations[0],
        )
        registry_file_uri = str((location or {}).get("Uri") or "")
        if not registry_file_uri:
            raise BiosInspectError(f"注册表 Location 缺少 Uri: {registry_uri}")

        registry, status = self._get(registry_file_uri)
        if registry is None:
            raise BiosInspectError(_describe_status(status, registry_file_uri))
        entries = (registry.get("RegistryEntries") or {}).get("Attributes") or []
        meta: Dict[str, dict] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("AttributeName") or "")
            if not name:
                continue
            if wanted is not None and name.lower() not in wanted:
                continue
            meta[name] = {
                "display_name": entry.get("DisplayName") or "",
                "menu_path": entry.get("MenuPath") or "",
            }
        return meta
