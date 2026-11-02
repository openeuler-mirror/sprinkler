"""数据访问层（DAO）包 — 按领域拆分。

子模块：
- ``base``    — 共享异常类（DbError/NotFoundError/...）+ rows_to_dicts 工具
- ``ledger``  — 三级台账 CRUD：cluster / cabinet / blade / import_cluster_tree / stats
- ``upgrade`` — 升级功能 CRUD：firmware_package / upgrade_task / upgrade_record

本 ``__init__`` 重新导出全部公共符号，保持 ``from sprinkler.db import dao`` 的
向后兼容——现有调用方（importer.py / cli.py / Tongming repository 等）无需修改。
"""

# 共享：异常类 + 工具
from sprinkler.db.dao.base import (
    DbError,
    UniqueError,
    ClusterExistsError,
    NotFoundError,
    rows_to_dicts,
)

# 台账域
from sprinkler.db.dao.ledger import (
    list_clusters,
    get_cluster,
    get_cluster_by_name,
    create_cluster,
    update_cluster,
    delete_cluster,
    list_cabinets,
    get_cabinet,
    create_cabinet,
    update_cabinet,
    delete_cabinet,
    list_blades,
    get_blade,
    create_blade,
    update_blade,
    delete_blade,
    import_cluster_tree,
    stats,
)

# 升级域
from sprinkler.db.dao.upgrade import (
    list_firmware_packages,
    get_firmware_package,
    create_firmware_package,
    delete_firmware_package,
    create_upgrade_task,
    update_upgrade_task,
    get_upgrade_task,
    list_upgrade_tasks,
    create_upgrade_record,
    update_upgrade_record,
    list_upgrade_records,
    list_pending_records,
)

# models 中的枚举常量也被调用方通过 dao.DEVICE_TYPES 访问，保持重新导出
from sprinkler.db.models import DEVICE_TYPES

__all__ = [
    # base
    "DbError", "UniqueError", "ClusterExistsError", "NotFoundError",
    "rows_to_dicts",
    # ledger
    "list_clusters", "get_cluster", "get_cluster_by_name", "create_cluster",
    "update_cluster", "delete_cluster",
    "list_cabinets", "get_cabinet", "create_cabinet", "update_cabinet",
    "delete_cabinet",
    "list_blades", "get_blade", "create_blade", "update_blade", "delete_blade",
    "import_cluster_tree", "stats",
    # upgrade
    "list_firmware_packages", "get_firmware_package", "create_firmware_package",
    "delete_firmware_package",
    "create_upgrade_task", "update_upgrade_task", "get_upgrade_task",
    "list_upgrade_tasks",
    "create_upgrade_record", "update_upgrade_record", "list_upgrade_records",
    "list_pending_records",
    # models 重新导出
    "DEVICE_TYPES",
]
