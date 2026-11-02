"""SQLite 连接与初始化。

连接策略：每次调用返回独立连接（sqlite3.Row 行工厂、外键强开、WAL 模式），
建表由 init_db 保证幂等（CREATE TABLE IF NOT EXISTS）。
"""

import sqlite3
from pathlib import Path

from sprinkler.db.models import ALL_DDL

DB_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = DB_DIR / "sprinkler.db"


def get_connection(db_path=None):
    """打开（或新建）SQLite 连接。

    :param db_path: 数据库文件路径；默认 ``DB_DIR / sprinkler.db``
    :return: sqlite3.Connection（Row 工厂、外键级联已开启、WAL 模式）
    """
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path=None):
    """初始化数据库：建库建表建索引（幂等），返回连接。"""
    conn = get_connection(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        for ddl in ALL_DDL:
            conn.execute(ddl)
        conn.commit()
        return conn
    except Exception:
        conn.close()
        raise
