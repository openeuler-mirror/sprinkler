"""三级台账表结构定义：cluster / cabinet / blade。

对应 design.md §3 的 DDL：唯一约束、外键级联、索引均在数据库层生效。
"""

from datetime import datetime, timezone


def utcnow() -> str:
    """当前 UTC 时间，ISO8601 字符串（如 2026-09-20T03:00:00Z）"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


TABLES = {
    "cluster": """
        CREATE TABLE IF NOT EXISTS cluster (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT    NOT NULL UNIQUE,
            description  TEXT    DEFAULT '',
            created_at   TEXT    NOT NULL,
            updated_at   TEXT    NOT NULL
        )
    """,
    "cabinet": """
        CREATE TABLE IF NOT EXISTS cabinet (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            cluster_id   INTEGER NOT NULL REFERENCES cluster(id) ON DELETE CASCADE,
            name         TEXT    NOT NULL,
            superpod_id  TEXT    NOT NULL,
            ca           TEXT    NOT NULL,
            frame_num    TEXT    DEFAULT '',
            order_index  INTEGER NOT NULL DEFAULT 0,
            created_at   TEXT    NOT NULL,
            UNIQUE (cluster_id, name)
        )
    """,
    "blade": """
        CREATE TABLE IF NOT EXISTS blade (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            cabinet_id   INTEGER NOT NULL REFERENCES cabinet(id) ON DELETE CASCADE,
            slot         TEXT    NOT NULL,
            board_type   TEXT    DEFAULT '',
            serial       TEXT    DEFAULT '',
            device_type  TEXT    NOT NULL,
            is_server    INTEGER NOT NULL DEFAULT 0,
            bmc_ip       TEXT    DEFAULT '',
            bmc_user     TEXT    DEFAULT 'Administrator',
            bmc_password TEXT    DEFAULT '',
            ubm_ip       TEXT    DEFAULT '',
            ubm_port     INTEGER,
            os_ip        TEXT    DEFAULT '',
            os_user      TEXT    DEFAULT 'root',
            os_password  TEXT    DEFAULT '',
            os_port      INTEGER DEFAULT 22,
            os_type      TEXT    DEFAULT '',
            created_at   TEXT    NOT NULL,
            updated_at   TEXT    NOT NULL,
            UNIQUE (cabinet_id, slot)
        )
    """,
    "firmware_package": """
        CREATE TABLE IF NOT EXISTS firmware_package (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT    NOT NULL,
            component    TEXT    NOT NULL,
            device_type  TEXT    NOT NULL,
            version      TEXT    DEFAULT '',
            file_path    TEXT    NOT NULL,
            file_size    INTEGER DEFAULT 0,
            md5_sum      TEXT    DEFAULT '',
            description  TEXT    DEFAULT '',
            created_at   TEXT    NOT NULL,
            UNIQUE (name, version)
        )
    """,
    "upgrade_task": """
        CREATE TABLE IF NOT EXISTS upgrade_task (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT    NOT NULL,
            scenario     TEXT    NOT NULL,
            cluster_id   INTEGER,
            status       TEXT    NOT NULL DEFAULT 'pending',
            strategy     TEXT    NOT NULL DEFAULT 'redfish',
            parallel     INTEGER NOT NULL DEFAULT 8,
            total        INTEGER NOT NULL DEFAULT 0,
            succeeded    INTEGER NOT NULL DEFAULT 0,
            failed       INTEGER NOT NULL DEFAULT 0,
            created_at   TEXT    NOT NULL,
            updated_at   TEXT    NOT NULL,
            finished_at  TEXT    DEFAULT '',
            FOREIGN KEY (cluster_id) REFERENCES cluster(id) ON DELETE SET NULL
        )
    """,
    "upgrade_record": """
        CREATE TABLE IF NOT EXISTS upgrade_record (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id      INTEGER NOT NULL REFERENCES upgrade_task(id) ON DELETE CASCADE,
            blade_id     INTEGER,
            host         TEXT    NOT NULL,
            device_type  TEXT    NOT NULL,
            component    TEXT    NOT NULL,
            package_id   INTEGER REFERENCES firmware_package(id) ON DELETE SET NULL,
            channel      TEXT    NOT NULL,
            status       TEXT    NOT NULL DEFAULT 'pending',
            version_before TEXT  DEFAULT '',
            version_after  TEXT  DEFAULT '',
            error_msg    TEXT    DEFAULT '',
            elapsed      REAL    DEFAULT 0,
            created_at   TEXT    NOT NULL,
            updated_at   TEXT    NOT NULL
        )
    """,
}

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_cabinet_cluster ON cabinet(cluster_id)",
    "CREATE INDEX IF NOT EXISTS idx_blade_cabinet   ON blade(cabinet_id)",
    "CREATE INDEX IF NOT EXISTS idx_upgrade_task_cluster ON upgrade_task(cluster_id)",
    "CREATE INDEX IF NOT EXISTS idx_upgrade_record_task   ON upgrade_record(task_id)",
    "CREATE INDEX IF NOT EXISTS idx_upgrade_record_blade   ON upgrade_record(blade_id)",
    "CREATE INDEX IF NOT EXISTS idx_firmware_component     ON firmware_package(component, device_type)",
]

ALL_DDL = list(TABLES.values()) + INDEX_SQL

#: 合法 device_type
DEVICE_TYPES = ("cpu", "npu", "sfu", "RoCE", "server")

CLUSTER_FIELDS = ("id", "name", "description", "created_at", "updated_at")
CABINET_FIELDS = ("id", "cluster_id", "name", "superpod_id", "ca",
                  "frame_num", "order_index", "created_at")
BLADE_FIELDS = ("id", "cabinet_id", "slot", "board_type", "serial", "device_type",
                "is_server", "bmc_ip", "bmc_user", "bmc_password",
                "ubm_ip", "ubm_port", "os_ip", "os_user", "os_password",
                "os_port", "os_type", "created_at", "updated_at")

FIRMWARE_PACKAGE_FIELDS = (
    "id", "name", "component", "device_type", "version",
    "file_path", "file_size", "md5_sum", "description", "created_at",
)
UPGRADE_TASK_FIELDS = (
    "id", "name", "scenario", "cluster_id", "status", "strategy",
    "parallel", "total", "succeeded", "failed",
    "created_at", "updated_at", "finished_at",
)
UPGRADE_RECORD_FIELDS = (
    "id", "task_id", "blade_id", "host", "device_type", "component",
    "package_id", "channel", "status", "version_before", "version_after",
    "error_msg", "elapsed", "created_at", "updated_at",
)

#: 合法 component 类型（升级组件）
COMPONENT_TYPES = (
    "ibmc", "bios", "cpld", "csr", "npu_firmware",
    "cc", "cfg", "telnet_crack",
)
#: 升级场景
UPGRADE_SCENARIOS = ("initial", "daily", "batch")
#: 升级策略
UPGRADE_STRATEGIES = ("auto", "redfish", "ssh_cli", "sol")
#: upgrade_task.status 合法值
TASK_STATUSES = ("pending", "running", "completed", "failed", "partial")
#: upgrade_record.status 合法值
RECORD_STATUSES = (
    "pending", "uploading", "upgrading", "polling",
    "verifying", "success", "failed", "skipped",
)
