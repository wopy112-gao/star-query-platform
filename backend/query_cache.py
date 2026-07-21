"""星宝语料场景查询系统 — 查询缓存（三层+）

第1层：精确匹配 — 原始问题完全一致
第2层：归一化匹配 — 去停用词后的核心词相同  
第3层：语义匹配 — entity+agg+dimension 组合键

v2: 语义 key 加入查询意图和维度维度（解决缓存下毒问题）
"""

import json
import re
import uuid
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import sqlite3

from config import settings
from domain_knowledge import kb

# 确保领域知识引擎已加载
if not kb.is_loaded:
    kb.load()

DB_FILE = Path(__file__).resolve().parent.parent / settings.HISTORY_DB_PATH

_lock = threading.Lock()


# ---- 归一化处理 ----

STOPWORDS = {"查询", "统计", "请问", "帮我", "查一下", "有", "的", "一下", "多少", "什么", "哪些", "哪个"}
SUFFIXES = {"场景数", "场景", "情况", "分布", "排名", "统计", "数据", "信息"}


def normalize(question: str) -> str:
    """归一化：去停用词、去后缀、去标点，提取核心词"""
    q = question.strip().lower()
    for w in sorted(STOPWORDS, key=len, reverse=True):
        q = q.replace(w, "")
    for w in sorted(SUFFIXES, key=len, reverse=True):
        q = q.replace(w, "")
    q = re.sub(r"[^\u4e00-\u9fff\w]", "", q).strip()
    return q


# ---- 查询意图提取（新增） ----


# 药品名中常见的噪声模式（患者口语化描述，非标准药品名）
_NOISE_DRUG_PATTERNS = [
    r'的$',           # 感冒的、发热的
    r'的药$',         # 感冒的药、消炎的药
    r'的那个$',       # 感冒的那个
    r'的片$',         # 感冒的片
    r'的胶囊$',       # 感冒的胶囊
    r'块钱的',        # 5块钱的感冒药
    r'^[0-9.]+',      # 数字开头的口语化描述
    r'^[a-zA-Z]+',    # 纯英文字母开头的噪声
]


def _is_noise_drug(drug_name: str) -> bool:
    """判断药品名是否为非标准噪声条目"""
    for pat in _NOISE_DRUG_PATTERNS:
        import re as _re
        if _re.search(pat, drug_name):
            return True
    return False


def _extract_entity(question: str) -> tuple[Optional[str], Optional[str]]:
    """从问题中提取实体（entity_type, entity_value）

    匹配策略：取所有匹配中字符最长的那个，同时过滤药品名中的噪声条目。
    
    这样"三九感冒灵"（4字药品）会优于"感冒"（2字疾病）；
    而单独的"感冒"不会匹配到噪声药名"感冒的"，正确归为疾病。
    """
    cleaned = question.strip().lower()
    best_type, best_value = None, None
    best_len = 0

    # 1. 城市（用城市白名单）
    for city in sorted(kb.city_names, key=len, reverse=True):
        if city in cleaned and len(city) > best_len:
            best_type, best_value, best_len = "city", city, len(city)

    # 2. 药品（长名优先，跳过噪声条目）
    #    注：短词（≤3字）如果也出现在疾病关键词中，留给疾病分类处理
    for drug in sorted(kb.drug_names, key=len, reverse=True):
        if drug in cleaned and len(drug) > best_len:
            if _is_noise_drug(drug):
                continue
            # 短词（≤3字）同时是疾病关键词→留给疾病分类
            if len(drug) <= 3 and drug in kb.disease_keywords:
                continue
            best_type, best_value, best_len = "drug", drug, len(drug)

    # 3. 疾病
    for kw in sorted(kb.disease_keywords, key=len, reverse=True):
        if kw in cleaned and len(kw) > best_len:
            best_type, best_value, best_len = "disease", kw, len(kw)

    return (best_type, best_value)


def _extract_aggregation(question: str) -> str:
    """提取查询聚合意图"""
    q = question.strip().lower()
    # 优先级高的先匹配
    if any(w in q for w in ['明细', '详情', '逐条', '罗列', '展示一下']):
        return "detail"
    if any(w in q for w in ['率']):
        return "rate"
    if any(w in q for w in ['分布', '呈现', '按', 'by']):
        return "distribution"
    if any(w in q for w in ['趋势', '月度', '月份', '按月']):
        return "trend"
    if any(w in q for w in ['排名', 'top', '排序']):
        return "ranking"
    # 默认：问总数
    return "total"


def _extract_dimension(question: str) -> Optional[str]:
    """提取分组维度"""
    q = question.strip().lower()
    if '省份' in q or '省分布' in q or q.endswith('省份'):
        return 'province'
    if '城市' in q or '地市' in q or '城市场景' in q:
        return 'city'
    if '门店' in q:
        return 'store'
    if '连锁' in q:
        return 'chain'
    if '月份' in q or '月度' in q or '按月' in q:
        return 'month'
    if '疾病' in q or '病种' in q:
        return 'disease'
    return None


def extract_semantic_key(question: str) -> str:
    """提取语义键：entity:val|agg:X|dim:Y

    v2 改进：加入聚合意图和维度维度，相同实体不同维度的查询不再互相污染。
    
    示例：
      "高血压的场景数" → disease:高血压|agg:total
      "高血压的场景数按省份" → disease:高血压|agg:distribution|dim:province
      "高血压的城市分布" → disease:高血压|agg:distribution|dim:city
      "诺欣妥场景明细" → drug:诺欣妥|agg:detail
      "无关键词" → raw:归一化文本
    """
    entity_type, entity_value = _extract_entity(question)
    agg = _extract_aggregation(question)
    dim = _extract_dimension(question)

    parts = []
    if entity_value:
        parts.append(f"{entity_type}:{entity_value}")
    parts.append(f"agg:{agg}")
    if dim:
        parts.append(f"dim:{dim}")

    if not entity_value and not dim:
        # 完全无匹配 → 用归一化键
        norm = normalize(question.strip().lower())
        return f"raw:{norm}" if norm else f"raw:{question.strip().lower()}"

    return "|".join(parts)


# ---- 缓存清理（新增） ----


def invalidate_by_question(question: str) -> int:
    """根据问题文本清除缓存（精准+语义两条路径）

    用于：
    - 用户踩了某个查询结果
    - 用户手动编辑 SQL 后需要更新缓存
    返回：清除的缓存条目数
    """
    exact = question.strip()
    norm = normalize(exact)
    sem = extract_semantic_key(exact)

    with _lock:
        conn = _get_conn()
        try:
            # 删除精确匹配的
            deleted = conn.execute(
                "DELETE FROM query_cache WHERE exact_key = ?",
                (exact,),
            ).rowcount

            # 也删除相同语义键的（用户换不同说法问同一件事）
            rows = conn.execute(
                "SELECT exact_key FROM query_cache WHERE semantic_key = ?",
                (sem,),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "DELETE FROM query_cache WHERE exact_key = ?",
                    (row["exact_key"],),
                )
                deleted += 1

            conn.commit()
            return deleted
        finally:
            conn.close()


# ============================================================
# 缓存信任等级（P1-②）
# ============================================================

TRUST_ORDER = {"ephemeral": 0, "confirmed": 1, "verified": 2}


def set_trust_level(
    identifier: str,
    target_level: str,
    by_intent_key: bool = False,
) -> bool:
    """设置指定缓存的信任等级

    Args:
        identifier: question（精确匹配）或 intent_key
        target_level: ephemeral / confirmed / verified
        by_intent_key: True 时按 intent_key 匹配，否则按 exact_key

    Returns:
        是否成功（entry 是否存在）
    """
    if target_level not in TRUST_ORDER:
        return False

    with _lock:
        conn = _get_conn()
        try:
            if by_intent_key:
                row = conn.execute(
                    "UPDATE query_cache SET trust_level = ? WHERE intent_key = ?",
                    (target_level, identifier),
                )
            else:
                row = conn.execute(
                    "UPDATE query_cache SET trust_level = ? WHERE exact_key = ?",
                    (target_level, identifier),
                )
            conn.commit()
            return row.rowcount > 0
        finally:
            conn.close()


def upgrade_trust(
    identifier: str,
    by_intent_key: bool = False,
) -> bool:
    """升级缓存信任等级（如 ephemeral → confirmed → verified）"""
    with _lock:
        conn = _get_conn()
        try:
            if by_intent_key:
                row = conn.execute(
                    "SELECT trust_level FROM query_cache WHERE intent_key = ?",
                    (identifier,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT trust_level FROM query_cache WHERE exact_key = ?",
                    (identifier,),
                ).fetchone()
            if not row:
                return False

            current = row["trust_level"]
            current_order = TRUST_ORDER.get(current, 0)
            new_levels = [k for k, v in TRUST_ORDER.items() if v > current_order]
            if not new_levels:
                return False  # 已经是最高等级

            new_level = new_levels[0]  # 升一级
            if by_intent_key:
                conn.execute(
                    "UPDATE query_cache SET trust_level = ? WHERE intent_key = ?",
                    (new_level, identifier),
                )
            else:
                conn.execute(
                    "UPDATE query_cache SET trust_level = ? WHERE exact_key = ?",
                    (new_level, identifier),
                )
            conn.commit()
            print(f"[Cache] 升级信任: {identifier[:40]}... → {new_level}")
            return True
        finally:
            conn.close()


def downgrade_trust(
    identifier: str,
    by_intent_key: bool = False,
    target_level: str = "ephemeral",
) -> bool:
    """降级缓存信任等级（如 verified → ephemeral）"""
    with _lock:
        conn = _get_conn()
        try:
            if by_intent_key:
                row = conn.execute(
                    "UPDATE query_cache SET trust_level = ? WHERE intent_key = ?",
                    (target_level, identifier),
                )
            else:
                row = conn.execute(
                    "UPDATE query_cache SET trust_level = ? WHERE exact_key = ?",
                    (target_level, identifier),
                )
            conn.commit()
            if row.rowcount > 0:
                print(f"[Cache] 降级信任: {identifier[:40]}... → {target_level}")
                return True
            return False
        finally:
            conn.close()


def clean_ephemeral_cache(max_age_days: int = 7) -> int:
    """清理过期的 ephemeral 缓存条目"""
    with _lock:
        conn = _get_conn()
        try:
            deleted = conn.execute(
                "DELETE FROM query_cache WHERE trust_level = 'ephemeral' "
                "AND last_asked_at < datetime('now', ?)",
                (f'-{max_age_days} days',),
            ).rowcount
            conn.commit()
            if deleted:
                print(f"[Cache] 清理 {deleted} 条过期 ephemeral 缓存")
            return deleted
        finally:
            conn.close()


def invalidate_by_history_id(history_id: str) -> bool:
    """根据历史记录 ID 清除对应的缓存（在前端编辑 SQL 时调用）"""
    with _lock:
        conn = _get_conn()
        try:
            # 从 query_history 表中找出原始问题
            row = conn.execute(
                "SELECT question FROM query_history WHERE id = ?",
                (history_id,),
            ).fetchone()
            if row:
                deleted = invalidate_by_question(row["question"])
                return deleted > 0
            return False
        finally:
            conn.close()


# ---- 数据库操作 ----


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _init_db():
    with _lock:
        conn = _get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS query_cache (
                    exact_key TEXT PRIMARY KEY,
                    normalized_key TEXT NOT NULL,
                    semantic_key TEXT NOT NULL DEFAULT '',
                    sql_text TEXT NOT NULL,
                    chart_type TEXT DEFAULT 'auto',
                    hits INTEGER DEFAULT 1,
                    first_asked_at TEXT NOT NULL,
                    last_asked_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cache_normalized
                ON query_cache(normalized_key)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cache_semantic
                ON query_cache(semantic_key)
            """)

            # ---- 缓存版本自检 ----
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            row = conn.execute(
                "SELECT value FROM cache_meta WHERE key = 'version'"
            ).fetchone()
            current = settings.CACHE_VERSION
            if row is None:
                old_count = conn.execute(
                    "SELECT COUNT(*) FROM query_cache"
                ).fetchone()[0]
                if old_count > 0:
                    conn.execute("DELETE FROM query_cache")
                    print(f"[Cache] 首次版本化，已清理 {old_count} 条旧缓存")
                conn.execute(
                    "INSERT INTO cache_meta (key, value) VALUES ('version', ?)",
                    (current,),
                )
            elif row[0] != current:
                deleted = conn.execute("DELETE FROM query_cache").rowcount
                conn.execute(
                    "UPDATE cache_meta SET value = ? WHERE key = 'version'",
                    (current,),
                )
                conn.commit()
                print(f"[Cache] 代码版本变更 (v{row[0]}→v{current})，已清理 {deleted} 条旧缓存")
            conn.commit()
        finally:
            conn.close()


_init_db()


def lookup(question: str) -> Optional[dict]:
    """三层缓存查找，优先精确匹配"""
    exact = question.strip()
    norm = normalize(exact)
    sem = extract_semantic_key(exact)

    with _lock:
        conn = _get_conn()
        try:
            # 第1层：精确匹配
            row = conn.execute(
                "SELECT sql_text, chart_type, hits FROM query_cache WHERE exact_key = ?",
                (exact,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE query_cache SET hits = hits + 1, last_asked_at = ? WHERE exact_key = ?",
                    (datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S"), exact),
                )
                conn.commit()
                return {"sql": row["sql_text"], "chart_type": row["chart_type"], "layer": 1, "hits": row["hits"]}

            # 第2层：归一化匹配
            row = conn.execute(
                "SELECT sql_text, chart_type, hits FROM query_cache WHERE normalized_key = ? ORDER BY hits DESC LIMIT 1",
                (norm,),
            ).fetchone()
            if row:
                return {"sql": row["sql_text"], "chart_type": row["chart_type"], "layer": 2, "hits": row["hits"]}

            # 第3层：语义匹配（v2：粒度更细的 key）
            if sem:
                row = conn.execute(
                    "SELECT sql_text, chart_type, hits FROM query_cache WHERE semantic_key = ? ORDER BY hits DESC LIMIT 1",
                    (sem,),
                ).fetchone()
                if row:
                    return {"sql": row["sql_text"], "chart_type": row["chart_type"], "layer": 3, "hits": row["hits"]}

            return None
        finally:
            conn.close()


def store(question: str, sql: str, chart_type: str = "auto") -> bool:
    """写入三层缓存"""
    exact = question.strip()
    norm = normalize(exact)
    sem = extract_semantic_key(exact)
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

    with _lock:
        conn = _get_conn()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO query_cache
                   (exact_key, normalized_key, semantic_key, sql_text, chart_type, hits, first_asked_at, last_asked_at)
                   VALUES (?, ?, ?, ?, ?, COALESCE((SELECT hits + 1 FROM query_cache WHERE exact_key = ?), 1), ?, ?)""",
                (exact, norm, sem, sql, chart_type, exact, now, now),
            )
            conn.commit()
            return True
        finally:
            conn.close()


def get_stats() -> dict:
    """获取缓存统计"""
    with _lock:
        conn = _get_conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM query_cache").fetchone()[0]
            total_hits = conn.execute("SELECT SUM(hits) FROM query_cache").fetchone()[0] or 0
            top = conn.execute(
                "SELECT exact_key, hits FROM query_cache ORDER BY hits DESC LIMIT 10"
            ).fetchall()
            return {
                "total_entries": total,
                "total_hits": total_hits,
                "top_queries": [{"question": r["exact_key"], "hits": r["hits"]} for r in top],
            }
        finally:
            conn.close()


# ============================================================
# 结构化缓存（新增，v3）
# ============================================================


def lookup_by_intent(
    intent_cache_key: str,
    min_trust: str = "confirmed",
) -> Optional[dict]:
    """基于结构化意图的缓存 key 查找

    相比 lookup(question) 的优势：
    - 同语义不同表述 → 同一 key
    - 不受问题措辞变化影响

    Args:
        intent_cache_key: QueryIntent.cache_key 生成的 md5

    Returns:
        {"sql", "chart_type"} 或 None

    trust_level 过滤规则：
    - min_trust="confirmed": 只返回 confirmed 和 verified 的缓存
    - min_trust="verified": 只返回 verified 的缓存
    - min_trust="ephemeral": 返回全部（含临时缓存）
    """
    min_order = TRUST_ORDER.get(min_trust, 1)  # 默认 confirmed

    with _lock:
        conn = _get_conn()
        try:
            # 按信任等级排序：verified > confirmed > ephemeral
            row = conn.execute(
                "SELECT sql_text, chart_type, hits, trust_level FROM query_cache "
                "WHERE intent_key = ? "
                "ORDER BY "
                "  CASE trust_level "
                "    WHEN 'verified' THEN 2 "
                "    WHEN 'confirmed' THEN 1 "
                "    ELSE 0 "
                "  END DESC, "
                "  hits DESC "
                "LIMIT 1",
                (intent_cache_key,),
            ).fetchone()
            if row:
                level_order = TRUST_ORDER.get(row["trust_level"], 0)
                if level_order >= min_order:
                    return {
                        "sql": row["sql_text"],
                        "chart_type": row["chart_type"],
                        "layer": "intent",
                        "trust_level": row["trust_level"],
                    }
            return None
        finally:
            conn.close()


def store_with_intent(
    question: str,
    sql: str,
    intent_cache_key: str,
    chart_type: str = "auto",
    trust_level: str = "confirmed",
) -> bool:
    """写入缓存（含结构化 key + 信任等级）

    同时写入精确匹配和结构化 key 两套索引。

    trust_level:
    - "confirmed": 模板匹配生成（默认）
    - "ephemeral": LLM fallback 生成（下次重新验证通过后才升级）
    - "verified": 用户点赞/人工审核
    """
    exact = question.strip()
    norm = normalize(exact)
    sem = extract_semantic_key(exact)
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

    # 信任等级校验
    if trust_level not in TRUST_ORDER:
        trust_level = "confirmed"

    with _lock:
        conn = _get_conn()
        try:
            # 如果已存在且信任等级更高（如 verified），不降级
            existing = conn.execute(
                "SELECT trust_level FROM query_cache WHERE intent_key = ?",
                (intent_cache_key,),
            ).fetchone()
            if existing:
                existing_order = TRUST_ORDER.get(existing["trust_level"], 0)
                new_order = TRUST_ORDER.get(trust_level, 1)
                if existing_order > new_order:
                    # 不降低已有更高等级缓存的等级
                    trust_level = existing["trust_level"]

            conn.execute(
                """INSERT OR REPLACE INTO query_cache
                   (exact_key, normalized_key, semantic_key, intent_key,
                    sql_text, chart_type, trust_level, hits, first_asked_at, last_asked_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?,
                   COALESCE((SELECT hits + 1 FROM query_cache WHERE exact_key = ?), 1), ?, ?)""",
                (exact, norm, sem, intent_cache_key, sql, chart_type, trust_level, exact, now, now),
            )
            conn.commit()
            return True
        finally:
            conn.close()


def _init_db_v3():
    """增加 intent_key 列 + trust_level 列（向后兼容）"""
    with _lock:
        conn = _get_conn()
        try:
            cols = conn.execute("PRAGMA table_info(query_cache)").fetchall()
            col_names = [c["name"] for c in cols]

            if "intent_key" not in col_names:
                conn.execute(
                    "ALTER TABLE query_cache ADD COLUMN intent_key TEXT DEFAULT ''"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_cache_intent "
                    "ON query_cache(intent_key)"
                )
                print("[Cache v3] 已增加 intent_key 列")

            if "trust_level" not in col_names:
                conn.execute(
                    "ALTER TABLE query_cache ADD COLUMN trust_level TEXT DEFAULT 'confirmed'"
                )
                # 现有缓存默认标记为 confirmed
                conn.execute(
                    "UPDATE query_cache SET trust_level = 'confirmed' WHERE trust_level IS NULL"
                )
                print("[Cache v3] 已增加 trust_level 列")

            conn.commit()
        except Exception as e:
            print(f"[Cache v3] 初始化失败: {e}")
        finally:
            conn.close()


_init_db_v3()
