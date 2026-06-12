"""星宝语料场景查询系统 — 查询反馈存储（SQLite）

记录用户对查询结果的赞/踩反馈，用于后续算法优化。
"""

import uuid
import threading
from datetime import datetime, timezone
from pathlib import Path

import sqlite3

from config import settings

# 数据库文件（与历史记录共用同一文件）
DB_FILE = Path(__file__).resolve().parent.parent / settings.HISTORY_DB_PATH

_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _init_db():
    """初始化 feedback 表"""
    with _lock:
        conn = _get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS query_feedback (
                    id TEXT PRIMARY KEY,
                    history_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    question TEXT NOT NULL,
                    sentiment TEXT NOT NULL CHECK(sentiment IN ('like', 'dislike')),
                    comment TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_feedback_history
                ON query_feedback(history_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_feedback_username
                ON query_feedback(username, created_at DESC)
            """)
            conn.commit()
        finally:
            conn.close()


# 初始化
_init_db()


def submit_feedback(
    history_id: str,
    username: str,
    question: str,
    sentiment: str,
    comment: str = "",
) -> dict:
    """提交反馈（如果同一 history_id+username 已存在，则覆盖）"""
    record_id = str(uuid.uuid4())[:8]
    created_at = datetime.now(timezone.utc).astimezone().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    with _lock:
        conn = _get_conn()
        try:
            # 检查是否已存在
            existing = conn.execute(
                "SELECT id FROM query_feedback WHERE history_id = ? AND username = ?",
                (history_id, username),
            ).fetchone()

            if existing:
                # 更新
                conn.execute(
                    """UPDATE query_feedback
                       SET sentiment = ?, comment = ?, created_at = ?
                       WHERE id = ?""",
                    (sentiment, comment, created_at, existing["id"]),
                )
                record_id = existing["id"]
            else:
                # 插入
                conn.execute(
                    """INSERT INTO query_feedback
                       (id, history_id, username, question, sentiment, comment, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (record_id, history_id, username, question, sentiment, comment, created_at),
                )
            conn.commit()
        finally:
            conn.close()

    return {
        "id": record_id,
        "history_id": history_id,
        "sentiment": sentiment,
        "comment": comment,
    }


def get_feedback_for_history(history_id: str, username: str) -> dict | None:
    """获取某条历史记录的反馈"""
    with _lock:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT id, sentiment, comment, created_at FROM query_feedback WHERE history_id = ? AND username = ?",
                (history_id, username),
            ).fetchone()
            if row:
                return {
                    "id": row["id"],
                    "history_id": history_id,
                    "sentiment": row["sentiment"],
                    "comment": row["comment"],
                    "created_at": row["created_at"],
                }
            return None
        finally:
            conn.close()


def delete_feedback(history_id: str, username: str) -> bool:
    """删除某条历史记录的反馈"""
    with _lock:
        conn = _get_conn()
        try:
            cursor = conn.execute(
                "DELETE FROM query_feedback WHERE history_id = ? AND username = ?",
                (history_id, username),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()


def get_feedback_stats(username: str | None = None) -> dict:
    """获取反馈统计（用于管理端查看）"""
    with _lock:
        conn = _get_conn()
        try:
            if username:
                row = conn.execute(
                    """SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN sentiment='like' THEN 1 ELSE 0 END) AS likes,
                        SUM(CASE WHEN sentiment='dislike' THEN 1 ELSE 0 END) AS dislikes
                    FROM query_feedback WHERE username = ?""",
                    (username,),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN sentiment='like' THEN 1 ELSE 0 END) AS likes,
                        SUM(CASE WHEN sentiment='dislike' THEN 1 ELSE 0 END) AS dislikes
                    FROM query_feedback"""
                ).fetchone()
            return {
                "total": row["total"] if row["total"] else 0,
                "likes": row["likes"] if row["likes"] else 0,
                "dislikes": row["dislikes"] if row["dislikes"] else 0,
            }
        finally:
            conn.close()
