"""星宝语料场景查询系统 — 下载记录存储

使用 SQLite 记录每次数据导出的信息，支持历史查询和追溯。
"""

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional


_DB_PATH = Path(__file__).resolve().parent / "download_records.db"
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """获取线程本地连接"""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(_DB_PATH))
        _local.conn.row_factory = sqlite3.Row
        _init_db(_local.conn)
    return _local.conn


def _init_db(conn: sqlite3.Connection):
    """初始化表结构"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS download_records (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            filters_json TEXT NOT NULL,
            row_count INTEGER DEFAULT 0,
            file_size_bytes INTEGER DEFAULT 0,
            file_name TEXT NOT NULL,
            elapsed_ms REAL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()


def add_record(
    username: str,
    filters: dict,
    row_count: int,
    file_size_bytes: int,
    file_name: str,
    elapsed_ms: float,
) -> dict:
    """添加下载记录"""
    import uuid
    record_id = uuid.uuid4().hex[:12]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = _get_conn()
    conn.execute(
        """INSERT INTO download_records
           (id, username, filters_json, row_count, file_size_bytes, file_name, elapsed_ms, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (record_id, username, json.dumps(filters, ensure_ascii=False),
         row_count, file_size_bytes, file_name, elapsed_ms, now),
    )
    conn.commit()

    return {
        "id": record_id,
        "username": username,
        "filters": filters,
        "row_count": row_count,
        "file_size_bytes": file_size_bytes,
        "file_name": file_name,
        "elapsed_ms": elapsed_ms,
        "created_at": now,
    }


def get_records(
    username: str,
    page: int = 1,
    limit: int = 20,
) -> dict:
    """获取下载记录列表（按时间倒序）"""
    conn = _get_conn()
    offset = (page - 1) * limit

    total = conn.execute(
        "SELECT COUNT(*) as cnt FROM download_records WHERE username = ?",
        (username,),
    ).fetchone()["cnt"]

    rows = conn.execute(
        """SELECT * FROM download_records
           WHERE username = ?
           ORDER BY created_at DESC
           LIMIT ? OFFSET ?""",
        (username, limit, offset),
    ).fetchall()

    items = []
    for r in rows:
        items.append({
            "id": r["id"],
            "username": r["username"],
            "filters": json.loads(r["filters_json"]),
            "row_count": r["row_count"],
            "file_size_bytes": r["file_size_bytes"],
            "file_size_mb": round(r["file_size_bytes"] / 1024 / 1024, 2),
            "file_name": r["file_name"],
            "elapsed_ms": r["elapsed_ms"],
            "created_at": r["created_at"],
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": (offset + limit) < total,
    }


def get_all_records(page: int = 1, limit: int = 20) -> dict:
    """获取全平台下载记录（admin用）"""
    conn = _get_conn()
    offset = (page - 1) * limit

    total = conn.execute(
        "SELECT COUNT(*) as cnt FROM download_records",
    ).fetchone()["cnt"]

    rows = conn.execute(
        """SELECT * FROM download_records
           ORDER BY created_at DESC
           LIMIT ? OFFSET ?""",
        (limit, offset),
    ).fetchall()

    items = []
    for r in rows:
        items.append({
            "id": r["id"],
            "username": r["username"],
            "filters": json.loads(r["filters_json"]),
            "row_count": r["row_count"],
            "file_size_bytes": r["file_size_bytes"],
            "file_size_mb": round(r["file_size_bytes"] / 1024 / 1024, 2),
            "file_name": r["file_name"],
            "elapsed_ms": r["elapsed_ms"],
            "created_at": r["created_at"],
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": (offset + limit) < total,
    }
