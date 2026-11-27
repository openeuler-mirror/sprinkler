"""BIOS 高性能配置巡检（只读）。

对外入口为 ``bios_inspect_execute``，对应命令行 ``-bios-inspect``：

- 通过 BMC Redfish 读取 BIOS 当前属性与 ``BiosAttributeRegistry``；
- 按 ``sprinkler/config/bios_whitelist.json`` 中与 BIOS 菜单一致的分类输出；
- 只展示当前值与 BMC 菜单路径，不做期望值比对；
- 不修改 BIOS、不重启服务器。
"""

from sprinkler.checker.bios.inspector import (
    BiosInspectError,
    BiosInspector,
    build_rows,
    build_reader,
    format_menu_path,
)
from sprinkler.checker.bios.runner import bios_inspect_execute
from sprinkler.checker.bios.whitelist import (
    DEFAULT_WHITELIST_PATH,
    Whitelist,
    WhitelistCategory,
    WhitelistItem,
    load_whitelist,
)

__all__ = [
    "BiosInspectError",
    "BiosInspector",
    "Whitelist",
    "WhitelistCategory",
    "WhitelistItem",
    "DEFAULT_WHITELIST_PATH",
    "bios_inspect_execute",
    "build_reader",
    "build_rows",
    "format_menu_path",
    "load_whitelist",
]
