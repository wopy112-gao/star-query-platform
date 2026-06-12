"""星宝数据平台 — 管理后台数据存储层

管理三张表（users / incidents / operation_logs）的 CRUD。
线程安全，与 password_store.py / login_store.py 同模式。
"""

import json
import uuid
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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
    """初始化三张管理表"""
    with _lock:
        conn = _get_conn()
        try:
            # ---- 用户表 ----
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    role TEXT NOT NULL DEFAULT 'user',
                    status TEXT NOT NULL DEFAULT 'active',
                    display_name TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    last_login_at TEXT,
                    note TEXT DEFAULT ''
                )
            """)
            # ---- 反馈事件表 ----
            conn.execute("""
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    env TEXT DEFAULT 'prod',
                    status TEXT NOT NULL DEFAULT 'pending',
                    question TEXT NOT NULL,
                    sql TEXT DEFAULT '',
                    error TEXT DEFAULT '',
                    warnings TEXT DEFAULT '[]',
                    intent_info TEXT DEFAULT '{}',
                    history_id TEXT DEFAULT '',
                    feedback_comment TEXT DEFAULT '',
                    root_cause TEXT DEFAULT '',
                    fix_proposal TEXT DEFAULT '',
                    resolved_at TEXT,
                    resolver TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
            """)
            # ---- 操作审计日志表 ----
            conn.execute("""
                CREATE TABLE IF NOT EXISTS operation_logs (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT DEFAULT '',
                    detail TEXT DEFAULT '',
                    ip_address TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
            """)
            # ---- 索引 ----
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_incidents_status
                ON incidents(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_incidents_created
                ON incidents(created_at DESC)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_incidents_type
                ON incidents(type)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_operation_logs_username
                ON operation_logs(username)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_operation_logs_created
                ON operation_logs(created_at DESC)
            """)
            conn.commit()
        finally:
            conn.close()


_init_db()


# ============================================================
# 数据库迁移 — 新增修复状态字段
# ============================================================

def _migrate():
    """为 incidents 表添加 fix 相关字段（兼容旧库）"""
    with _lock:
        conn = _get_conn()
        try:
            # 检查列是否存在
            cols = {r[1] for r in conn.execute("PRAGMA table_info(incidents)").fetchall()}
            if "fix_status" not in cols:
                conn.execute("ALTER TABLE incidents ADD COLUMN fix_status TEXT DEFAULT ''")
            if "fix_attempted_at" not in cols:
                conn.execute("ALTER TABLE incidents ADD COLUMN fix_attempted_at TEXT DEFAULT ''")
            if "verification_note" not in cols:
                conn.execute("ALTER TABLE incidents ADD COLUMN verification_note TEXT DEFAULT ''")
            conn.commit()
            print("[DB Migration] incidents 表新增 fix_status/fix_attempted_at/verification_note 字段")
        finally:
            conn.close()


_migrate()


# ============================================================
# 帮助函数
# ============================================================

def _now() -> str:
    """返回当前时间的标准字符串"""
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _uid(prefix: str = "") -> str:
    """生成短 UID"""
    return prefix + uuid.uuid4().hex[:12]


# ============================================================
# Users — CRUD
# ============================================================

def get_users(
    page: int = 1,
    limit: int = 20,
    keyword: str = "",
    status_filter: str = "",
) -> dict:
    """获取用户列表（分页，可选搜索+状态筛选）"""
    with _lock:
        conn = _get_conn()
        try:
            where_parts = []
            params: list = []

            if keyword:
                where_parts.append("(username LIKE ? OR display_name LIKE ?)")
                kw = f"%{keyword}%"
                params.extend([kw, kw])

            if status_filter:
                where_parts.append("status = ?")
                params.append(status_filter)

            where = ""
            if where_parts:
                where = "WHERE " + " AND ".join(where_parts)

            total = conn.execute(
                f"SELECT COUNT(*) FROM users {where}",
                params,
            ).fetchone()[0]

            offset = (page - 1) * limit
            rows = conn.execute(
                f"""SELECT username, role, status, display_name,
                           created_at, last_login_at, note
                    FROM users {where}
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?""",
                params + [limit, offset],
            ).fetchall()

            items = [dict(r) for r in rows]

            # 补充查询次数
            for item in items:
                count_row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM query_history WHERE username = ?",
                    (item["username"],),
                ).fetchone()
                item["query_count"] = count_row["cnt"] if count_row else 0

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


def create_user(
    username: str,
    role: str = "user",
    display_name: str = "",
    note: str = "",
) -> dict:
    """新增用户（仅写入 users 表，密码由 auth 体系管理）"""
    with _lock:
        conn = _get_conn()
        try:
            now = _now()
            conn.execute(
                """INSERT INTO users (username, role, status, display_name, created_at, note)
                   VALUES (?, ?, 'active', ?, ?, ?)""",
                (username, role, display_name, now, note),
            )
            conn.commit()
            return {
                "username": username,
                "role": role,
                "status": "active",
                "display_name": display_name,
                "created_at": now,
                "note": note,
            }
        except sqlite3.IntegrityError:
            raise ValueError(f"用户「{username}」已存在")
        finally:
            conn.close()


def update_user(
    username: str,
    role: Optional[str] = None,
    display_name: Optional[str] = None,
    status: Optional[str] = None,
    note: Optional[str] = None,
) -> bool:
    """更新用户信息"""
    with _lock:
        conn = _get_conn()
        try:
            updates = []
            params: list = []

            if role is not None:
                updates.append("role = ?")
                params.append(role)
            if display_name is not None:
                updates.append("display_name = ?")
                params.append(display_name)
            if status is not None:
                updates.append("status = ?")
                params.append(status)
            if note is not None:
                updates.append("note = ?")
                params.append(note)

            if not updates:
                return False

            params.append(username)
            conn.execute(
                f"UPDATE users SET {', '.join(updates)} WHERE username = ?",
                params,
            )
            conn.commit()
            return conn.total_changes > 0
        finally:
            conn.close()


def delete_user(username: str) -> bool:
    """删除用户"""
    if username == "admin":
        return False  # 禁止删除 admin
    with _lock:
        conn = _get_conn()
        try:
            conn.execute("DELETE FROM users WHERE username = ?", (username,))
            conn.commit()
            return conn.total_changes > 0
        finally:
            conn.close()


def toggle_user_status(username: str) -> str:
    """切换用户启用/禁用状态，返回新状态"""
    with _lock:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT status FROM users WHERE username = ?",
                (username,),
            ).fetchone()
            if not row:
                raise ValueError(f"用户「{username}」不存在")
            new_status = "disabled" if row["status"] == "active" else "active"
            conn.execute(
                "UPDATE users SET status = ? WHERE username = ?",
                (new_status, username),
            )
            conn.commit()
            return new_status
        finally:
            conn.close()


def get_user(username: str) -> Optional[dict]:
    """获取单个用户信息"""
    with _lock:
        conn = _get_conn()
        try:
            row = conn.execute(
                """SELECT username, role, status, display_name,
                          created_at, last_login_at, note
                   FROM users WHERE username = ?""",
                (username,),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            count_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM query_history WHERE username = ?",
                (username,),
            ).fetchone()
            item["query_count"] = count_row["cnt"] if count_row else 0
            return item
        finally:
            conn.close()


def update_last_login(username: str):
    """更新用户最后登录时间"""
    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                "UPDATE users SET last_login_at = ? WHERE username = ?",
                (_now(), username),
            )
            conn.commit()
        finally:
            conn.close()


def is_user_disabled(username: str) -> bool:
    """检查用户是否被禁用"""
    with _lock:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT status FROM users WHERE username = ?",
                (username,),
            ).fetchone()
            if not row:
                return False  # 不存在 = 不禁用（由 auth 层处理）
            return row["status"] == "disabled"
        finally:
            conn.close()


# ============================================================
# Users — 统计
# ============================================================

def get_user_stats() -> dict:
    """获取用户统计数据"""
    with _lock:
        conn = _get_conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            active = conn.execute(
                "SELECT COUNT(*) FROM users WHERE status = 'active'"
            ).fetchone()[0]
            # 今日活跃（有查询记录）
            today = _now()[:10]
            today_active = conn.execute(
                """SELECT COUNT(DISTINCT username) FROM query_history
                   WHERE created_at LIKE ?""",
                (f"{today}%",),
            ).fetchone()[0]
            return {
                "total_users": total,
                "active_users": active,
                "today_active_users": today_active,
            }
        finally:
            conn.close()


# ============================================================
# Incidents — CRUD
# ============================================================

def get_incidents(
    page: int = 1,
    limit: int = 20,
    type_filter: str = "",
    status_filter: str = "",
    env_filter: str = "",
    keyword: str = "",
    date_from: str = "",
    date_to: str = "",
) -> dict:
    """获取反馈事件列表（带多条件筛选）"""
    with _lock:
        conn = _get_conn()
        try:
            where_parts = []
            params: list = []

            if type_filter:
                where_parts.append("type = ?")
                params.append(type_filter)
            if status_filter:
                where_parts.append("status = ?")
                params.append(status_filter)
            if env_filter:
                where_parts.append("env = ?")
                params.append(env_filter)
            if keyword:
                where_parts.append("question LIKE ?")
                params.append(f"%{keyword}%")
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
                f"SELECT COUNT(*) FROM incidents {where}",
                params,
            ).fetchone()[0]

            offset = (page - 1) * limit
            rows = conn.execute(
                f"""SELECT id, type, env, status, question,
                           error, warnings, history_id, feedback_comment,
                           root_cause, fix_proposal, resolved_at, resolver, created_at,
                           fix_status, fix_attempted_at, verification_note
                    FROM incidents {where}
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?""",
                params + [limit, offset],
            ).fetchall()

            items = []
            for r in rows:
                d = dict(r)
                # 解析 JSON 字段
                try:
                    d["warnings"] = json.loads(d.get("warnings", "[]"))
                except (json.JSONDecodeError, TypeError):
                    d["warnings"] = []
                items.append(d)

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


def get_incident(incident_id: str) -> Optional[dict]:
    """获取单条事件完整详情"""
    with _lock:
        conn = _get_conn()
        try:
            row = conn.execute(
                """SELECT * FROM incidents WHERE id = ?""",
                (incident_id,),
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            # 解析 JSON 字段
            for field in ("warnings", "intent_info"):
                try:
                    d[field] = json.loads(d.get(field, "{}"))
                except (json.JSONDecodeError, TypeError):
                    d[field] = {} if field == "intent_info" else []
            return d
        finally:
            conn.close()


def update_incident_status(
    incident_id: str,
    new_status: str,
    resolver: str = "",
) -> bool:
    """变更事件状态（同时清空 fix_status，避免状态残留在界面上）"""
    with _lock:
        conn = _get_conn()
        try:
            now = _now()
            resolved_at = now if new_status in ("resolved", "wontfix") else None
            conn.execute(
                """UPDATE incidents SET status = ?, resolved_at = ?,
                       resolver = ?, fix_status = '', fix_attempted_at = '',
                       verification_note = ''
                   WHERE id = ?""",
                (new_status, resolved_at, resolver, incident_id),
            )
            conn.commit()
            return conn.total_changes > 0
        finally:
            conn.close()


def batch_update_incident_status(
    ids: list[str],
    new_status: str,
    resolver: str = "",
) -> int:
    """批量变更事件状态（同时清空 fix_status）"""
    with _lock:
        conn = _get_conn()
        try:
            now = _now()
            resolved_at = now if new_status in ("resolved", "wontfix") else None
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"""UPDATE incidents SET status = ?, resolved_at = ?,
                       resolver = ?, fix_status = '', fix_attempted_at = '',
                       verification_note = ''
                   WHERE id IN ({placeholders})""",
                [new_status, resolved_at, resolver] + ids,
            )
            conn.commit()
            return conn.total_changes
        finally:
            conn.close()


def batch_delete_incidents(ids: list[str]) -> int:
    """批量删除事件"""
    with _lock:
        conn = _get_conn()
        try:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"DELETE FROM incidents WHERE id IN ({placeholders})",
                ids,
            )
            conn.commit()
            return conn.total_changes
        finally:
            conn.close()


def delete_incident(incident_id: str) -> bool:
    """删除单条事件"""
    with _lock:
        conn = _get_conn()
        try:
            conn.execute("DELETE FROM incidents WHERE id = ?", (incident_id,))
            conn.commit()
            return conn.total_changes > 0
        finally:
            conn.close()


def update_incident_analysis(
    incident_id: str,
    root_cause: str,
    fix_proposal: str,
) -> bool:
    """写入 AI 分析结果"""
    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                """UPDATE incidents SET root_cause = ?, fix_proposal = ?,
                       status = 'analyzed' WHERE id = ?""",
                (root_cause, fix_proposal, incident_id),
            )
            conn.commit()
            return conn.total_changes > 0
        finally:
            conn.close()


def update_incident_fix_status(
    incident_id: str,
    fix_status: str,
    verification_note: str = "",
) -> bool:
    """更新修复执行状态

    fix_status: '' / fix_applying / fix_verified / fix_failed
    """
    with _lock:
        conn = _get_conn()
        try:
            now = _now()
            conn.execute(
                """UPDATE incidents SET fix_status = ?,
                       fix_attempted_at = ?,
                       verification_note = ?
                   WHERE id = ?""",
                (fix_status, now, verification_note, incident_id),
            )
            conn.commit()
            return conn.total_changes > 0
        finally:
            conn.close()


def import_incident(incident: dict) -> bool:
    """导入一条事件（迁移用）"""
    with _lock:
        conn = _get_conn()
        try:
            warnings_json = json.dumps(incident.get("warnings", []), ensure_ascii=False)
            intent_info_json = json.dumps(
                incident.get("intent_info", {}), ensure_ascii=False
            )
            conn.execute(
                """INSERT OR IGNORE INTO incidents
                   (id, type, env, status, question, sql, error, warnings,
                    intent_info, history_id, feedback_comment,
                    root_cause, fix_proposal, resolved_at, resolver, created_at,
                    fix_status, verification_note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    incident.get("incident_id") or incident.get("id", ""),
                    incident.get("type", ""),
                    incident.get("env", "prod"),
                    incident.get("status", "pending"),
                    incident.get("question", ""),
                    incident.get("sql", ""),
                    incident.get("error", ""),
                    warnings_json,
                    intent_info_json,
                    incident.get("history_id", ""),
                    incident.get("feedback_comment", ""),
                    incident.get("root_cause", ""),
                    incident.get("fix_proposal", ""),
                    incident.get("resolved_at"),
                    incident.get("resolver", ""),
                    incident.get("created_at", _now()),
                    incident.get("fix_status", ""),
                    incident.get("verification_note", ""),
                ),
            )
            conn.commit()
            return conn.total_changes > 0
        finally:
            conn.close()


# ============================================================
# Incidents — 统计
# ============================================================

def get_incident_stats() -> dict:
    """获取事件统计数据"""
    with _lock:
        conn = _get_conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
            by_status = conn.execute(
                """SELECT status, COUNT(*) AS cnt FROM incidents
                   GROUP BY status ORDER BY cnt DESC"""
            ).fetchall()
            by_type = conn.execute(
                """SELECT type, COUNT(*) AS cnt FROM incidents
                   GROUP BY type ORDER BY cnt DESC"""
            ).fetchall()
            pending = conn.execute(
                "SELECT COUNT(*) FROM incidents WHERE status = 'pending'"
            ).fetchone()[0]
            return {
                "total": total,
                "pending": pending,
                "by_status": {r["status"]: r["cnt"] for r in by_status},
                "by_type": {r["type"]: r["cnt"] for r in by_type},
            }
        finally:
            conn.close()


# ============================================================
# Operation Logs
# ============================================================

def add_operation_log(
    username: str,
    action: str,
    target: str = "",
    detail: str = "",
    ip_address: str = "",
) -> dict:
    """记录一条操作日志"""
    with _lock:
        conn = _get_conn()
        try:
            log_id = _uid("op_")
            now = _now()
            conn.execute(
                """INSERT INTO operation_logs
                   (id, username, action, target, detail, ip_address, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (log_id, username, action, target, detail, ip_address, now),
            )
            conn.commit()
            return {
                "id": log_id,
                "username": username,
                "action": action,
                "target": target,
                "detail": detail,
                "created_at": now,
            }
        finally:
            conn.close()


def get_operation_logs(
    page: int = 1,
    limit: int = 20,
    username_filter: str = "",
    action_filter: str = "",
) -> dict:
    """获取操作日志列表"""
    with _lock:
        conn = _get_conn()
        try:
            where_parts = []
            params: list = []

            if username_filter:
                where_parts.append("username = ?")
                params.append(username_filter)
            if action_filter:
                where_parts.append("action = ?")
                params.append(action_filter)

            where = ""
            if where_parts:
                where = "WHERE " + " AND ".join(where_parts)

            total = conn.execute(
                f"SELECT COUNT(*) FROM operation_logs {where}",
                params,
            ).fetchone()[0]

            offset = (page - 1) * limit
            rows = conn.execute(
                f"""SELECT id, username, action, target, detail, ip_address, created_at
                    FROM operation_logs {where}
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?""",
                params + [limit, offset],
            ).fetchall()

            has_more = (offset + limit) < total

            return {
                "items": [dict(r) for r in rows],
                "total": total,
                "page": page,
                "limit": limit,
                "has_more": has_more,
            }
        finally:
            conn.close()


# ============================================================
# Dashboard 总览
# ============================================================

def get_dashboard_stats() -> dict:
    """获取管理后台总览指标"""
    with _lock:
        conn = _get_conn()
        try:
            # 用户统计
            total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            today = _now()[:10]
            today_active = conn.execute(
                """SELECT COUNT(DISTINCT username) FROM query_history
                   WHERE created_at LIKE ?""",
                (f"{today}%",),
            ).fetchone()[0]

            # 事件统计
            pending_incidents = conn.execute(
                "SELECT COUNT(*) FROM incidents WHERE status = 'pending'"
            ).fetchone()[0]

            # 总查询次数
            total_queries = conn.execute(
                "SELECT COUNT(*) FROM query_history"
            ).fetchone()[0]

            # 近7日查询量趋势
            trend = conn.execute(
                """SELECT SUBSTR(created_at, 1, 10) AS date,
                          COUNT(*) AS count
                   FROM query_history
                   WHERE created_at >= date('now', '-7 days')
                   GROUP BY date
                   ORDER BY date"""
            ).fetchall()

            return {
                "total_users": total_users,
                "today_active_users": today_active,
                "pending_incidents": pending_incidents,
                "total_queries": total_queries,
                "trend": [
                    {"date": r["date"], "count": r["count"]} for r in trend
                ],
            }
        finally:
            conn.close()
