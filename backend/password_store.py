"""星宝语料场景查询系统 — 密码修改存储（SQLite）

密码查询优先级：password_override > .env 默认值
即用户改过密码后，以 override 表为准；未改过的仍使用 .env 初始密码。
"""

import uuid
import threading
from pathlib import Path

import sqlite3

from config import settings

DB_FILE = Path(__file__).resolve().parent.parent / settings.HISTORY_DB_PATH

_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _init_db():
    """初始化密码覆盖表"""
    with _lock:
        conn = _get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS password_override (
                    username TEXT PRIMARY KEY,
                    hashed_password TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.commit()
        finally:
            conn.close()


_init_db()


def get_override(username: str) -> str | None:
    """获取用户已修改过的密码哈希，没有则返回 None"""
    with _lock:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT hashed_password FROM password_override WHERE username = ?",
                (username,),
            ).fetchone()
            return row["hashed_password"] if row else None
        finally:
            conn.close()


def set_override(username: str, hashed_password: str) -> bool:
    """写入/更新密码覆盖记录"""
    from datetime import datetime, timezone
    with _lock:
        conn = _get_conn()
        try:
            now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                """INSERT OR REPLACE INTO password_override
                   (username, hashed_password, updated_at)
                   VALUES (?, ?, ?)""",
                (username, hashed_password, now),
            )
            conn.commit()
            return True
        finally:
            conn.close()


def delete_override(username: str) -> bool:
    """删除密码覆盖（恢复为 .env 默认密码）"""
    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                "DELETE FROM password_override WHERE username = ?",
                (username,),
            )
            deleted = conn.total_changes > 0
            conn.commit()
            return deleted
        finally:
            conn.close()
