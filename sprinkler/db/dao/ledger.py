"""三级台账数据访问：cluster / cabinet / blade CRUD、级联删除、事务化覆盖导入。

所有函数接收 sqlite3.Connection（由 database.get_connection() 提供），
不在此模块内管理连接生命周期，便于业务层按事务组合。
"""

from sprinkler.db.models import utcnow, DEVICE_TYPES, BLADE_FIELDS
from sprinkler.db.dao.base import (
    DbError, UniqueError, ClusterExistsError, NotFoundError, rows_to_dicts,
)


# ----------------------------- cluster -----------------------------

def list_clusters(conn, keyword=None, page=None, size=None):
    """集群列表；含各集群机柜计数与更新时间，支持关键字过滤与分页。"""
    where = ""
    params = []
    if keyword:
        where = "WHERE c.name LIKE ?"
        params.append(f"%{keyword}%")
    sql = (
        "SELECT c.id, c.name, c.description, c.created_at, c.updated_at, "
        "       COUNT(k.id) AS cabinet_count "
        "FROM cluster c LEFT JOIN cabinet k ON k.cluster_id = c.id "
        f"{where} "
        "GROUP BY c.id "
        "ORDER BY c.updated_at DESC, c.id DESC"
    )
    if page and size:
        offset = (page - 1) * size
        sql += " LIMIT ? OFFSET ?"
        params.extend([size, offset])
    rows = conn.execute(sql, params).fetchall()
    total_params = [f"%{keyword}%"] if keyword else []
    total = conn.execute(
        "SELECT COUNT(*) FROM cluster c" + (f" {where}" if where else ""), total_params
    ).fetchone()[0]
    return rows_to_dicts(rows), total


def get_cluster(conn, cluster_id):
    row = conn.execute("SELECT * FROM cluster WHERE id = ?", (cluster_id,)).fetchone()
    return dict(row) if row else None


def get_cluster_by_name(conn, name):
    row = conn.execute("SELECT * FROM cluster WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def create_cluster(conn, name, description=""):
    now = utcnow()
    cur = conn.execute(
        "INSERT INTO cluster (name, description, created_at, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (name, description, now, now),
    )
    conn.commit()
    return cur.lastrowid


def update_cluster(conn, cluster_id, name=None, description=None):
    row = conn.execute("SELECT * FROM cluster WHERE id = ?", (cluster_id,)).fetchone()
    if not row:
        raise NotFoundError("cluster", cluster_id)
    new_name = row["name"] if name is None else name
    new_desc = row["description"] if description is None else description
    conn.execute(
        "UPDATE cluster SET name = ?, description = ?, updated_at = ? WHERE id = ?",
        (new_name, new_desc, utcnow(), cluster_id),
    )
    conn.commit()
    return get_cluster(conn, cluster_id)


def delete_cluster(conn, cluster_id):
    if not conn.execute("SELECT 1 FROM cluster WHERE id = ?", (cluster_id,)).fetchone():
        raise NotFoundError("cluster", cluster_id)
    conn.execute("DELETE FROM cluster WHERE id = ?", (cluster_id,))
    conn.commit()


# ----------------------------- cabinet -----------------------------

def list_cabinets(conn, cluster_id):
    rows = conn.execute(
        "SELECT * FROM cabinet WHERE cluster_id = ? ORDER BY order_index, id", (cluster_id,)
    ).fetchall()
    return rows_to_dicts(rows)


def get_cabinet(conn, cabinet_id):
    row = conn.execute("SELECT * FROM cabinet WHERE id = ?", (cabinet_id,)).fetchone()
    return dict(row) if row else None


def create_cabinet(conn, cluster_id, name, superpod_id, ca, frame_num="", order_index=0):
    cur = conn.execute(
        "INSERT INTO cabinet (cluster_id, name, superpod_id, ca, frame_num, order_index, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cluster_id, name, superpod_id, ca, frame_num, order_index, utcnow()),
    )
    conn.commit()
    return cur.lastrowid


def update_cabinet(conn, cabinet_id, name=None, superpod_id=None, ca=None,
                   frame_num=None, order_index=None):
    row = conn.execute("SELECT * FROM cabinet WHERE id = ?", (cabinet_id,)).fetchone()
    if not row:
        raise NotFoundError("cabinet", cabinet_id)
    conn.execute(
        "UPDATE cabinet SET name = ?, superpod_id = ?, ca = ?, frame_num = ?, "
        "order_index = ? WHERE id = ?",
        (row["name"] if name is None else name,
         row["superpod_id"] if superpod_id is None else superpod_id,
         row["ca"] if ca is None else ca,
         row["frame_num"] if frame_num is None else frame_num,
         row["order_index"] if order_index is None else order_index,
         cabinet_id),
    )
    conn.commit()
    return get_cabinet(conn, cabinet_id)


def delete_cabinet(conn, cabinet_id):
    if not conn.execute("SELECT 1 FROM cabinet WHERE id = ?", (cabinet_id,)).fetchone():
        raise NotFoundError("cabinet", cabinet_id)
    conn.execute("DELETE FROM cabinet WHERE id = ?", (cabinet_id,))
    conn.commit()


# ----------------------------- blade -----------------------------

def list_blades(conn, cabinet_id, device_type=None):
    sql = "SELECT * FROM blade WHERE cabinet_id = ?"
    params = [cabinet_id]
    if device_type:
        sql += " AND device_type = ?"
        params.append(device_type)
    sql += " ORDER BY slot"
    rows = conn.execute(sql, params).fetchall()
    return rows_to_dicts(rows)


def get_blade(conn, blade_id):
    row = conn.execute("SELECT * FROM blade WHERE id = ?", (blade_id,)).fetchone()
    return dict(row) if row else None


def create_blade(conn, cabinet_id, slot, device_type, board_type="", serial="",
                 is_server=False, bmc_ip="", bmc_user="Administrator",
                 bmc_password="", ubm_ip="", ubm_port=None,
                 os_ip="", os_user="root", os_password="", os_port=22, os_type=""):
    if device_type not in DEVICE_TYPES:
        raise ValueError(f"非法 device_type: {device_type}")
    now = utcnow()
    cur = conn.execute(
        "INSERT INTO blade (cabinet_id, slot, board_type, serial, device_type, is_server, "
        "bmc_ip, bmc_user, bmc_password, ubm_ip, ubm_port, "
        "os_ip, os_user, os_password, os_port, os_type, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cabinet_id, slot, board_type, serial, device_type, 1 if is_server else 0,
         bmc_ip, bmc_user, bmc_password, ubm_ip, ubm_port,
         os_ip, os_user, os_password, os_port, os_type, now, now),
    )
    conn.commit()
    return cur.lastrowid


def update_blade(conn, blade_id, **fields):
    """按字段更新刀片台账；仅允许 BLADE_FIELDS 中除主外键与时间戳外的字段。"""
    row = conn.execute("SELECT * FROM blade WHERE id = ?", (blade_id,)).fetchone()
    if not row:
        raise NotFoundError("blade", blade_id)
    allowed = set(BLADE_FIELDS) - {"id", "cabinet_id", "created_at", "updated_at"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"非法 blade 字段: {sorted(unknown)}")
    if "device_type" in fields and fields["device_type"] not in DEVICE_TYPES:
        raise ValueError(f"非法 device_type: {fields['device_type']}")
    data = dict(row)
    for k, v in fields.items():
        data[k] = v
    data["is_server"] = 1 if data.get("is_server") else 0
    conn.execute(
        "UPDATE blade SET board_type = ?, serial = ?, device_type = ?, is_server = ?, "
        "bmc_ip = ?, bmc_user = ?, bmc_password = ?, ubm_ip = ?, ubm_port = ?, "
        "os_ip = ?, os_user = ?, os_password = ?, os_port = ?, os_type = ?, updated_at = ? "
        "WHERE id = ?",
        (data["board_type"], data["serial"], data["device_type"], data["is_server"],
         data["bmc_ip"], data["bmc_user"], data["bmc_password"], data["ubm_ip"],
         data["ubm_port"], data["os_ip"], data["os_user"], data["os_password"],
         data["os_port"], data["os_type"], utcnow(), blade_id),
    )
    conn.commit()
    return get_blade(conn, blade_id)


def delete_blade(conn, blade_id):
    if not conn.execute("SELECT 1 FROM blade WHERE id = ?", (blade_id,)).fetchone():
        raise NotFoundError("blade", blade_id)
    conn.execute("DELETE FROM blade WHERE id = ?", (blade_id,))
    conn.commit()


# ----------------------------- 事务化导入 -----------------------------

def import_cluster_tree(conn, cluster, cabinets, overwrite=False):
    """单事务导入三级台账（集群 + 机柜 + 刀片），支持覆盖重建。

    :param conn: 需由调用方保证事务边界（本函数不 commit/rollback，
                 发生异常时回滚由调用方或抛出的 sqlite3.Error 触发）
    :param cluster: dict{name, description}
    :param cabinets: list[dict{name, superpod_id, ca, frame_num?, order_index?, blades}]
    :param overwrite: True 时删除已存在同名集群旧链路并重建
    :return: dict{cluster_id, cluster, cabinets, blades}
    :raises ClusterExistsError: 集群名已存在且 overwrite=False
    """
    name = cluster["name"]
    existing = conn.execute("SELECT id FROM cluster WHERE name = ?", (name,)).fetchone()
    if existing:
        if not overwrite:
            raise ClusterExistsError(name)
        conn.execute("DELETE FROM cluster WHERE id = ?", (existing["id"],))

    now = utcnow()
    cluster_id = conn.execute(
        "INSERT INTO cluster (name, description, created_at, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (cluster["name"], cluster.get("description", ""), now, now),
    ).lastrowid

    cabinet_count = 0
    blade_count = 0
    for i, cabinet in enumerate(cabinets):
        cab_id = conn.execute(
            "INSERT INTO cabinet (cluster_id, name, superpod_id, ca, frame_num, order_index, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cluster_id, cabinet["name"], cabinet["superpod_id"], cabinet["ca"],
             cabinet.get("frame_num", ""), cabinet.get("order_index", i), now),
        ).lastrowid
        cabinet_count += 1
        for blade in cabinet.get("blades", []):
            conn.execute(
                "INSERT INTO blade (cabinet_id, slot, board_type, serial, device_type, is_server, "
                "bmc_ip, bmc_user, bmc_password, ubm_ip, ubm_port, "
                "os_ip, os_user, os_password, os_port, os_type, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cab_id, blade["slot"], blade.get("board_type", ""),
                 blade.get("serial", ""), blade["device_type"],
                 1 if blade.get("is_server") else 0,
                 blade.get("bmc_ip", ""), blade.get("bmc_user", "Administrator"),
                 blade.get("bmc_password", ""), blade.get("ubm_ip", ""),
                 blade.get("ubm_port"), blade.get("os_ip", ""),
                 blade.get("os_user", "root"), blade.get("os_password", ""),
                 blade.get("os_port", 22), blade.get("os_type", ""), now, now),
            )
            blade_count += 1

    return {
        "cluster_id": cluster_id,
        "cluster": 1,
        "cabinets": cabinet_count,
        "blades": blade_count,
    }


# ----------------------------- stats -----------------------------

def stats(conn):
    out = {}
    for table in ("cluster", "cabinet", "blade"):
        out[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    out["blade_by_type"] = rows_to_dicts(
        conn.execute(
            "SELECT device_type, COUNT(*) AS n FROM blade GROUP BY device_type ORDER BY n DESC"
        ).fetchall()
    )
    return out
