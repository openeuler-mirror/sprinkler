"""BIOS 巡检白名单的加载与别名解析。

白名单以 JSON 维护，结构为 ``categories[].attributes[]``。每个条目用逻辑名
``name`` 描述一个巡检项，并用 ``aliases`` 列出不同机型/固件上可能出现的真实
``AttributeName``——BIOS 注册表字段名在不同平台并不统一（例如 x86 Purley 的
电源策略是 ``CustomPowerPolicy``，部分鲲鹏平台是 ``PowerPolicy``），因此按
别名匹配比写死单个字段名更可靠。

本白名单仅用于展示当前值与菜单路径，不包含期望值，也不做判定。
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from sprinkler.utils.utils import package_path

DEFAULT_WHITELIST_PATH = package_path() / "config" / "bios_whitelist.json"


@dataclass(frozen=True)
class WhitelistItem:
    """一个逻辑巡检项。"""

    name: str
    category: str
    aliases: Tuple[str, ...]
    zh_name: str = ""
    menu_path: str = ""
    description: str = ""
    reference: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class WhitelistCategory:
    name: str
    description: str
    items: Tuple[WhitelistItem, ...]


@dataclass(frozen=True)
class Whitelist:
    path: Path
    categories: Tuple[WhitelistCategory, ...]
    items: Tuple[WhitelistItem, ...]
    alias_index: Dict[str, WhitelistItem]

    def aliases(self) -> Set[str]:
        """当前白名单内所有条目的别名（小写），供按需解析注册表使用。"""
        return {alias.lower() for item in self.items for alias in item.aliases}

    def select(self, keywords: Optional[Iterable[str]] = None) -> Tuple["Whitelist", List[str]]:
        """筛选巡检项。

        - ``keywords`` 为空：只保留 ``enabled=true`` 的条目；
        - ``keywords`` 非空：按逻辑名或别名（大小写不敏感）匹配，忽略 enabled。

        :return: 筛选后的白名单，以及未匹配到任何条目的关键字。
        """
        wanted = [str(keyword).strip() for keyword in (keywords or []) if str(keyword).strip()]
        lowered = [keyword.lower() for keyword in wanted]
        matched: Set[str] = set()
        categories = []
        items = []
        alias_index: Dict[str, WhitelistItem] = {}
        for category in self.categories:
            category_items = []
            for item in category.items:
                aliases = {alias.lower() for alias in item.aliases}
                if lowered:
                    hits = [keyword for keyword in lowered if keyword in aliases]
                    if not hits:
                        continue
                    matched.update(hits)
                elif not item.enabled:
                    continue
                category_items.append(item)
                items.append(item)
                for alias in aliases:
                    alias_index.setdefault(alias, item)
            if category_items:
                categories.append(WhitelistCategory(
                    name=category.name,
                    description=category.description,
                    items=tuple(category_items),
                ))
        missing = [keyword for keyword in wanted if keyword.lower() not in matched]
        return Whitelist(
            path=self.path,
            categories=tuple(categories),
            items=tuple(items),
            alias_index=alias_index,
        ), missing

    def match(self, attribute_name: str) -> Optional[WhitelistItem]:
        """按真实属性名（大小写不敏感）查找巡检项。"""
        return self.alias_index.get(str(attribute_name or "").strip().lower())


def resolve_whitelist_path(value: Optional[str]) -> Path:
    """返回白名单路径；未指定时使用包内默认配置。"""
    if value:
        return Path(str(value)).expanduser()
    return DEFAULT_WHITELIST_PATH


def _as_tuple(value) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, Iterable):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def load_whitelist(path: Optional[str] = None) -> Whitelist:
    """读取白名单并建立别名索引。

    :raises ValueError: 文件不存在、结构非法、条目缺少 name 或别名冲突
    """
    whitelist_path = resolve_whitelist_path(path)
    if not whitelist_path.is_file():
        raise ValueError(f"BIOS 白名单文件不存在: {whitelist_path}")
    try:
        data = json.loads(whitelist_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"BIOS 白名单 JSON 解析失败: {whitelist_path}: {exc}") from exc

    raw_categories = data.get("categories")
    if not isinstance(raw_categories, list):
        raise ValueError("BIOS 白名单格式错误：categories 必须是数组")

    categories = []
    items = []
    alias_index: Dict[str, WhitelistItem] = {}
    for raw_category in raw_categories:
        if not isinstance(raw_category, dict):
            continue
        category_name = str(raw_category.get("name") or "未分类").strip()
        category_items = []
        for spec in raw_category.get("attributes") or []:
            if not isinstance(spec, dict):
                continue
            name = str(spec.get("name") or "").strip()
            if not name:
                raise ValueError(f"BIOS 白名单条目缺少 name（分类 {category_name}）")
            aliases = _as_tuple(spec.get("aliases"))
            if name not in aliases:
                aliases = (name,) + aliases
            item = WhitelistItem(
                name=name,
                category=category_name,
                aliases=aliases,
                zh_name=str(spec.get("zh_name") or "").strip(),
                menu_path=str(spec.get("menu_path") or "").strip(),
                description=str(spec.get("description") or "").strip(),
                reference=str(spec.get("reference") or "").strip(),
                enabled=bool(spec.get("enabled", True)),
            )
            seen_keys = set()
            for alias in aliases:
                key = alias.strip().lower()
                if not key or key in seen_keys:
                    # 别名匹配大小写不敏感；同一巡检项内的 ConfigTDP/ConfigTdp
                    # 这类仅大小写不同的写法视为同一个别名。
                    continue
                seen_keys.add(key)
                if key in alias_index:
                    other = alias_index[key]
                    raise ValueError(
                        f"BIOS 白名单别名冲突: {alias!r} 同时属于 {other.name!r} 与 {name!r}"
                    )
                alias_index[key] = item
            category_items.append(item)
            items.append(item)
        categories.append(WhitelistCategory(
            name=category_name,
            description=str(raw_category.get("description") or "").strip(),
            items=tuple(category_items),
        ))

    if not items:
        raise ValueError(f"BIOS 白名单未定义任何属性: {whitelist_path}")
    return Whitelist(
        path=whitelist_path,
        categories=tuple(categories),
        items=tuple(items),
        alias_index=alias_index,
    )
