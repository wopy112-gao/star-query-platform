"""星宝语料场景查询系统 — 查询历史记录（SQLite 存储）"""

import json
import uuid
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import sqlite3

from config import settings

# 数据库文件路径
DB_FILE = Path(__file__).resolve().parent.parent / settings.HISTORY_DB_PATH
# 旧 JSON 文件路径（用于迁移）
OLD_JSON_FILE = Path(__file__).resolve().parent.parent / "query_history.json"

# 每页默认条数
DEFAULT_PAGE_LIMIT = 20
# 每个用户最多保留条数（从配置读取）
MAX_HISTORY_PER_USER = settings.MAX_HISTORY

_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    """获取数据库连接（线程安全）"""
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _init_db():
    """初始化数据库表结构"""
    with _lock:
        conn = _get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS query_history (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    question TEXT NOT NULL,
                    sql TEXT DEFAULT '',
                    elapsed_ms REAL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    success INTEGER DEFAULT 1
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_history_username_created
                ON query_history(username, created_at DESC)
            """)
            conn.commit()
        finally:
            conn.close()


def _migrate_from_json():
    """从旧 JSON 文件迁移数据到 SQLite"""
    if not OLD_JSON_FILE.exists():
        return

    with _lock:
        try:
            with open(OLD_JSON_FILE, "r", encoding="utf-8") as f:
                items = json.load(f)
        except (json.JSONDecodeError, IOError):
            items = []

        if not items:
            return

        conn = _get_conn()
        try:
            # 检查是否已有数据，避免重复迁移
            count = conn.execute("SELECT COUNT(*) FROM query_history").fetchone()[0]
            if count > 0:
                # 已有数据，跳过迁移
                return

            for item in items:
                conn.execute(
                    """INSERT OR IGNORE INTO query_history
                       (id, username, question, sql, elapsed_ms, created_at, success)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item.get("id", str(uuid.uuid4())[:8]),
                        "admin",  # 旧数据统一标记为 admin
                        item.get("question", ""),
                        item.get("sql", ""),
                        item.get("elapsed_ms", 0),
                        item.get("created_at", ""),
                        1 if item.get("success", True) else 0,
                    ),
                )
            conn.commit()

            # 迁移完后改名
            bak_path = OLD_JSON_FILE.with_suffix(".json.bak")
            OLD_JSON_FILE.rename(bak_path)
            print(f"[历史] 从 JSON 迁移 {len(items)} 条记录，旧文件已重命名为 {bak_path.name}")
        finally:
            conn.close()


def _enforce_limit(username: str):
    """强制每个用户的历史记录不超过上限（保留最新的 N 条）"""
    conn = _get_conn()
    try:
        while True:
            row = conn.execute(
                """SELECT COUNT(*) AS cnt FROM query_history WHERE username = ?""",
                (username,),
            ).fetchone()
            if row["cnt"] <= MAX_HISTORY_PER_USER:
                break
            # 删除最旧的一条
            conn.execute(
                """DELETE FROM query_history
                   WHERE id = (
                       SELECT id FROM query_history
                       WHERE username = ?
                       ORDER BY created_at ASC
                       LIMIT 1
                   )""",
                (username,),
            )
            conn.commit()
    finally:
        conn.close()


# 启动时初始化
_init_db()
_migrate_from_json()


def add_history(
    question: str,
    sql: str,
    elapsed_ms: float,
    username: str = "admin",
    success: bool = True,
) -> dict:
    """添加一条历史记录"""
    with _lock:
        conn = _get_conn()
        try:
            record_id = str(uuid.uuid4())[:8]
            created_at = datetime.now(timezone.utc).astimezone().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            conn.execute(
                """INSERT INTO query_history
                   (id, username, question, sql, elapsed_ms, created_at, success)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (record_id, username, question, sql, elapsed_ms, created_at, 1 if success else 0),
            )
            conn.commit()
        finally:
            conn.close()

    # 超出限制自动清理
    _enforce_limit(username)

    return {
        "id": record_id,
        "question": question,
        "sql": sql,
        "elapsed_ms": round(elapsed_ms, 2),
        "created_at": created_at,
        "success": success,
    }


def get_history(
    username: str,
    page: int = 1,
    limit: int = DEFAULT_PAGE_LIMIT,
    keyword: Optional[str] = None,
) -> dict:
    """获取用户的历史记录（分页 + 可选搜索）"""
    with _lock:
        conn = _get_conn()
        try:
            # 构建查询条件
            where_clause = "username = ?"
            params: list = [username]

            if keyword:
                where_clause += " AND question LIKE ?"
                params.append(f"%{keyword}%")

            # 查总数
            total = conn.execute(
                f"SELECT COUNT(*) FROM query_history WHERE {where_clause}",
                params,
            ).fetchone()[0]

            # 分页查询
            offset = (page - 1) * limit
            rows = conn.execute(
                f"""SELECT id, question, sql, elapsed_ms, created_at, success
                    FROM query_history
                    WHERE {where_clause}
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?""",
                params + [limit, offset],
            ).fetchall()

            items = [
                {
                    "id": r["id"],
                    "question": r["question"],
                    "sql": r["sql"],
                    "elapsed_ms": r["elapsed_ms"],
                    "created_at": r["created_at"],
                    "success": bool(r["success"]),
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


def delete_history(history_id: str, username: str) -> bool:
    """删除单条历史记录（需校验归属）"""
    with _lock:
        conn = _get_conn()
        try:
            cursor = conn.execute(
                "DELETE FROM query_history WHERE id = ? AND username = ?",
                (history_id, username),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()


def clear_history(username: str) -> int:
    """清空指定用户的所有历史记录"""
    with _lock:
        conn = _get_conn()
        try:
            cursor = conn.execute(
                "DELETE FROM query_history WHERE username = ?",
                (username,),
            )
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()


def get_all_history(
    page: int = 1,
    limit: int = 20,
    username: str = "",
    keyword: str = "",
    date_from: str = "",
    date_to: str = "",
) -> dict:
    """管理后台 — 跨用户全量查询记录（分页 + 多维度筛选）"""
    with _lock:
        conn = _get_conn()
        try:
            where_parts: list[str] = []
            params: list = []

            if username:
                where_parts.append("username = ?")
                params.append(username)

            if keyword:
                where_parts.append("(question LIKE ? OR sql LIKE ?)")
                kw = f"%{keyword}%"
                params.extend([kw, kw])

            if date_from:
                where_parts.append("created_at >= ?")
                params.append(date_from)

            if date_to:
                where_parts.append("created_at <= ?")
                params.append(date_to + " 23:59:59")

            where = ""
            if where_parts:
                where = "WHERE " + " AND ".join(where_parts)

            total = conn.execute(
                f"SELECT COUNT(*) FROM query_history {where}",
                params,
            ).fetchone()[0]

            offset = (page - 1) * limit
            rows = conn.execute(
                f"""SELECT id, username, question, sql, elapsed_ms, created_at, success
                    FROM query_history {where}
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?""",
                params + [limit, offset],
            ).fetchall()

            items = [
                {
                    "id": r["id"],
                    "username": r["username"],
                    "question": r["question"],
                    "sql": r["sql"],
                    "elapsed_ms": r["elapsed_ms"],
                    "created_at": r["created_at"],
                    "success": bool(r["success"]),
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
