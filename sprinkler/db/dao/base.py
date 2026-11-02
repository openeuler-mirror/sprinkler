"""DAO 共享基础设施：异常类 + 行转换工具。

所有领域 DAO（ledger / upgrade）均依赖此模块的异常类和 rows_to_dicts。
保持异常类在此处集中定义，避免领域模块间循环依赖。
"""


class DbError(Exception):
    pass


class UniqueError(DbError):
    def __init__(self, table, field, value):
        self.table = table
        self.field = field
        self.value = value
        super().__init__(f"{table}.{field} 已存在: {value}")


class ClusterExistsError(DbError):
    def __init__(self, name):
        self.name = name
        super().__init__(f"集群已存在: {name}（可指定 overwrite=True 覆盖重建）")


class NotFoundError(DbError):
    def __init__(self, table, obj_id):
        self.table = table
        self.obj_id = obj_id
        super().__init__(f"{table} 不存在: id={obj_id}")


def rows_to_dicts(rows):
    """将 sqlite3.Row 列表转为 dict 列表。"""
    return [dict(r) for r in rows]
