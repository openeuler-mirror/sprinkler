"""升级功能数据访问：firmware_package / upgrade_task / upgrade_record CRUD。

所有函数接收 sqlite3.Connection（由 database.get_connection() 提供），
不在此模块内管理连接生命周期，便于业务层按事务组合。
"""

from sprinkler.db.models import (
    utcnow,
    COMPONENT_TYPES, UPGRADE_SCENARIOS, UPGRADE_STRATEGIES,
    TASK_STATUSES, RECORD_STATUSES,
    FIRMWARE_PACKAGE_FIELDS, UPGRADE_TASK_FIELDS, UPGRADE_RECORD_FIELDS,
)
from sprinkler.db.dao.base import NotFoundError, rows_to_dicts


# ----------------------------- firmware_package -----------------------------

def list_firmware_packages(conn, component=None, device_type=None):
    """列出固件包，支持按组件类型和设备类型过滤。

    :return: list[dict]，按 created_at DESC 排序
    """
    where_parts, params = [], []
    if component:
        where_parts.append("component = ?")
        params.append(component)
    if device_type:
        where_parts.append("device_type = ?")
        params.append(device_type)
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    sql = f"SELECT * FROM firmware_package {where} ORDER BY created_at DESC, id DESC"
    rows = conn.execute(sql, params).fetchall()
    return rows_to_dicts(rows)


def get_firmware_package(conn, package_id):
    """获取固件包详情；不存在返回 None。"""
    row = conn.execute(
        "SELECT * FROM firmware_package WHERE id = ?", (package_id,)
    ).fetchone()
    return dict(row) if row else None


def create_firmware_package(conn, name, component, device_type, version,
                            file_path, file_size=0, md5_sum="", description=""):
    """登记固件包；同名同版本已存在时返回已有 id（幂等）。

    :raises ValueError: component 不在 COMPONENT_TYPES 中
    :return: package_id
    """
    if component not in COMPONENT_TYPES:
        raise ValueError(f"非法 component: {component}")
    existing = conn.execute(
        "SELECT id FROM firmware_package WHERE name = ? AND version = ?",
        (name, version),
    ).fetchone()
    if existing:
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO firmware_package "
        "(name, component, device_type, version, file_path, file_size, "
        " md5_sum, description, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name, component, device_type, version, file_path, file_size,
         md5_sum, description, utcnow()),
    )
    conn.commit()
    return cur.lastrowid


def delete_firmware_package(conn, package_id):
    """删除固件包记录（不删物理文件）。

    :raises NotFoundError: package_id 不存在
    """
    if not conn.execute(
        "SELECT 1 FROM firmware_package WHERE id = ?", (package_id,)
    ).fetchone():
        raise NotFoundError("firmware_package", package_id)
    conn.execute("DELETE FROM firmware_package WHERE id = ?", (package_id,))
    conn.commit()


# ----------------------------- upgrade_task -----------------------------

def create_upgrade_task(conn, name, scenario, cluster_id=None,
                        strategy="redfish", parallel=8):
    """创建升级任务，返回 task_id。

    :raises ValueError: scenario/strategy 不在合法枚举中
    :return: task_id
    """
    if scenario not in UPGRADE_SCENARIOS:
        raise ValueError(f"非法 scenario: {scenario}")
    if strategy not in UPGRADE_STRATEGIES:
        raise ValueError(f"非法 strategy: {strategy}")
    now = utcnow()
    cur = conn.execute(
        "INSERT INTO upgrade_task "
        "(name, scenario, cluster_id, status, strategy, parallel, "
        " total, succeeded, failed, created_at, updated_at) "
        "VALUES (?, ?, ?, 'pending', ?, ?, 0, 0, 0, ?, ?)",
        (name, scenario, cluster_id, strategy, parallel, now, now),
    )
    conn.commit()
    return cur.lastrowid


def update_upgrade_task(conn, task_id, **fields):
    """更新任务状态/计数；仅允许 UPGRADE_TASK_FIELDS 中除主键和时间戳外的字段。

    :raises NotFoundError: task_id 不存在
    :raises ValueError: 字段不在白名单中，或 status 值不合法
    """
    row = conn.execute(
        "SELECT * FROM upgrade_task WHERE id = ?", (task_id,)
    ).fetchone()
    if not row:
        raise NotFoundError("upgrade_task", task_id)
    allowed = set(UPGRADE_TASK_FIELDS) - {"id", "created_at"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"非法 upgrade_task 字段: {sorted(unknown)}")
    if "status" in fields and fields["status"] not in TASK_STATUSES:
        raise ValueError(f"非法 task status: {fields['status']}")
    if "scenario" in fields and fields["scenario"] not in UPGRADE_SCENARIOS:
        raise ValueError(f"非法 scenario: {fields['scenario']}")
    data = dict(row)
    data.update(fields)
    data["updated_at"] = utcnow()
    # 逐字段构造 UPDATE（动态 SET）
    set_cols = []
    set_vals = []
    for col in allowed:
        if col in fields or col == "updated_at":
            set_cols.append(f"{col} = ?")
            set_vals.append(data[col])
    set_vals.append(task_id)
    conn.execute(
        f"UPDATE upgrade_task SET {', '.join(set_cols)} WHERE id = ?",
        set_vals,
    )
    conn.commit()
    return get_upgrade_task(conn, task_id)


def get_upgrade_task(conn, task_id):
    row = conn.execute(
        "SELECT * FROM upgrade_task WHERE id = ?", (task_id,)
    ).fetchone()
    return dict(row) if row else None


def list_upgrade_tasks(conn, cluster_id=None, scenario=None, limit=50):
    """列出升级任务，按 updated_at DESC 排序。"""
    where_parts, params = [], []
    if cluster_id is not None:
        where_parts.append("cluster_id = ?")
        params.append(cluster_id)
    if scenario:
        where_parts.append("scenario = ?")
        params.append(scenario)
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    sql = (
        f"SELECT * FROM upgrade_task {where} "
        f"ORDER BY updated_at DESC, id DESC LIMIT ?"
    )
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return rows_to_dicts(rows)


# ----------------------------- upgrade_record -----------------------------

def create_upgrade_record(conn, task_id, blade_id, host, device_type, component,
                          package_id, channel):
    """创建升级记录，返回 record_id。

    :raises ValueError: component/channel 不合法
    :raises NotFoundError: task_id 不存在
    """
    if component not in COMPONENT_TYPES:
        raise ValueError(f"非法 component: {component}")
    if channel not in ("redfish", "ssh_cli", "sol"):
        raise ValueError(f"非法 channel: {channel}")
    if not conn.execute(
        "SELECT 1 FROM upgrade_task WHERE id = ?", (task_id,)
    ).fetchone():
        raise NotFoundError("upgrade_task", task_id)
    now = utcnow()
    cur = conn.execute(
        "INSERT INTO upgrade_record "
        "(task_id, blade_id, host, device_type, component, package_id, "
        " channel, status, version_before, version_after, error_msg, "
        " elapsed, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', '', '', '', 0, ?, ?)",
        (task_id, blade_id, host, device_type, component, package_id,
         channel, now, now),
    )
    conn.commit()
    return cur.lastrowid


def update_upgrade_record(conn, record_id, **fields):
    """更新升级记录状态/版本/错误信息。

    :raises NotFoundError: record_id 不存在
    :raises ValueError: 字段不在白名单中，或 status 值不合法
    """
    row = conn.execute(
        "SELECT * FROM upgrade_record WHERE id = ?", (record_id,)
    ).fetchone()
    if not row:
        raise NotFoundError("upgrade_record", record_id)
    allowed = set(UPGRADE_RECORD_FIELDS) - {"id", "task_id", "created_at"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"非法 upgrade_record 字段: {sorted(unknown)}")
    if "status" in fields and fields["status"] not in RECORD_STATUSES:
        raise ValueError(f"非法 record status: {fields['status']}")
    data = dict(row)
    data.update(fields)
    data["updated_at"] = utcnow()
    set_cols = []
    set_vals = []
    for col in allowed:
        if col in fields or col == "updated_at":
            set_cols.append(f"{col} = ?")
            set_vals.append(data[col])
    set_vals.append(record_id)
    conn.execute(
        f"UPDATE upgrade_record SET {', '.join(set_cols)} WHERE id = ?",
        set_vals,
    )
    conn.commit()


def list_upgrade_records(conn, task_id):
    """列出某任务的所有升级记录，按 id 排序。"""
    rows = conn.execute(
        "SELECT * FROM upgrade_record WHERE task_id = ? ORDER BY id",
        (task_id,),
    ).fetchall()
    return rows_to_dicts(rows)


def list_pending_records(conn, task_id):
    """列出某任务中状态为 pending/uploading/upgrading/polling/verifying 的记录
    （用于 --resume 恢复中断的升级任务）。"""
    active = ("pending", "uploading", "upgrading", "polling", "verifying")
    placeholders = ",".join("?" * len(active))
    rows = conn.execute(
        f"SELECT * FROM upgrade_record WHERE task_id = ? AND status IN ({placeholders}) "
        "ORDER BY id",
        (task_id, *active),
    ).fetchall()
    return rows_to_dicts(rows)
