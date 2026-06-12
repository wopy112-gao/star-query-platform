"""星宝语料场景查询系统 — 登录日志（SQLite 存储）"""

import uuid
import threading
from datetime import datetime, timezone
from pathlib import Path

import sqlite3

from config import settings

DB_FILE = Path(__file__).resolve().parent.parent / settings.HISTORY_DB_PATH

_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    """获取数据库连接（线程安全）"""
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _init_db():
    """初始化登录日志表"""
    with _lock:
        conn = _get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS login_log (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    ip_address TEXT DEFAULT '',
                    user_agent TEXT DEFAULT '',
                    success INTEGER DEFAULT 1,
                    detail TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_login_log_created
                ON login_log(created_at DESC)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_login_log_username
                ON login_log(username)
            """)
            conn.commit()
        finally:
            conn.close()


_init_db()


def add_login_log(
    username: str,
    ip_address: str = "",
    user_agent: str = "",
    success: bool = True,
    detail: str = "",
) -> dict:
    """记录一条登录日志"""
    with _lock:
        conn = _get_conn()
        try:
            record_id = str(uuid.uuid4())[:8]
            created_at = datetime.now(timezone.utc).astimezone().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            conn.execute(
                """INSERT INTO login_log
                   (id, username, ip_address, user_agent, success, detail, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (record_id, username, ip_address, user_agent, 1 if success else 0, detail, created_at),
            )
            conn.commit()
            return {
                "id": record_id,
                "username": username,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "success": success,
                "detail": detail,
                "created_at": created_at,
            }
        finally:
            conn.close()


def get_login_logs(
    page: int = 1,
    limit: int = 20,
    username_filter: str = "",
) -> dict:
    """获取登录日志（分页，可选按用户名过滤）"""
    with _lock:
        conn = _get_conn()
        try:
            where_clause = ""
            params: list = []

            if username_filter:
                where_clause = "WHERE username = ?"
                params = [username_filter]

            total = conn.execute(
                f"SELECT COUNT(*) FROM login_log {where_clause}",
                params,
            ).fetchone()[0]

            offset = (page - 1) * limit
            rows = conn.execute(
                f"""SELECT id, username, ip_address, user_agent, success, detail, created_at
                    FROM login_log
                    {where_clause}
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?""",
                params + [limit, offset],
            ).fetchall()

            items = [
                {
                    "id": r["id"],
                    "username": r["username"],
                    "ip_address": r["ip_address"],
                    "user_agent": r["user_agent"],
                    "success": bool(r["success"]),
                    "detail": r["detail"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]

            has_more = (offset + limit) < total

            return {
                "items": items,
                "total": total,
                "page": page,
                "limit": limit,
                "has_more": has_more,
            }
        finally:
            conn.close()
