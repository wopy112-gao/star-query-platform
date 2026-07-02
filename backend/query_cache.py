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

# 停用词：使用负向零宽断言，避免破坏"有没有"等完整词汇
STOPWORDS = {"查询", "统计", "请问", "帮我", "查一下", "有", "一下", "多少", "什么", "哪些", "哪个"}
SUFFIXES = {"场景数", "场景", "情况", "分布", "排名", "统计", "数据", "信息"}


def normalize(question: str) -> str:
    """归一化：去停用词、去后缀、去标点，提取核心词
    
    修复：使用词边界匹配，避免"有没有"被拆成"没"和"有"
    """
    q = question.strip().lower()
    
    # 先保护"有没有"不被拆分
    q = q.replace("有没有", "HAS_QUESTION")
    
    # 去停用词（精确匹配，不做子串替换）
    for w in sorted(STOPWORDS, key=len, reverse=True):
        # 使用词边界：前后都是非中文字符或字符串边界
        pattern = rf"(?<!\w){re.escape(w)}(?!\w)"
        q = re.sub(pattern, " ", q)
    
    # 去后缀
    for w in sorted(SUFFIXES, key=len, reverse=True):
        pattern = rf"(?<!\w){re.escape(w)}(?!\w)"
        q = re.sub(pattern, " ", q)
    
    # 恢复"有没有"
    q = q.replace("HAS_QUESTION", "")
    
    # 清理多余空格和标点
    q = re.sub(r"[^\u4e00-\u9fff\w]", " ", q).strip()
    q = re.sub(r"\s+", " ", q)
    
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
        if re.search(pat, drug_name):
            return True
    return False


def _extract_entity(question: str) -> tuple[Optional[str], Optional[str]]:
    """从问题中提取实体（entity_type, entity_value）

    匹配策略：取所有匹配中字符最长的那个，同时过滤药品名中的噪声条目。
    
    这样"三九感冒灵"（4字药品）会优于"感冒"（2字疾病）；
    而单独的"感冒"不会匹配到噪声药名"感冒的"，正确归为疾病。
    """
    cleaned = question.strip().lower()
    best_type, best_value, best_len = None, None, 0

    # 1. 城市（用城市白名单）
    for city in sorted(kb.city_names, key=len, reverse=True):
        if city in cleaned and len(city) > best_len:
            best_type, best_value, best_len = "city", city, len(city)

    # 2. 药品（长名优先，跳过噪声条目）
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
    if '药品' in q or '产品' in q:
        return 'drug'
    if '性别' in q:
        return 'gender'
    if '年龄' in q:
        return 'age'
    return None


def extract_semantic_key(question: str) -> str:
    """提取语义 key：entity + agg + dimension
    
    将自然语言问题转化为结构化的语义 key，用于缓存匹配。
    
    优势：
    - "感冒各省场景数" 和 "按省份展示感冒场景数" → 同一 key
    - "感冒" 和 "发热的药" → 不同 key（entity 不同）
    - "各省场景数" 和 "按城市场景数" → 不同 key（dimension 不同）
    
    实现：
    1. 提取实体（药品/疾病/城市）→ entity_value
    2. 提取聚合意图 → agg
    3. 提取维度 → dimension
    4. 组合后 md5
    
    这样即使问题措辞变化，只要实体+聚合+维度不变，就能命中缓存。
    """
    entity_type, entity_value = _extract_entity(question)
    agg = _extract_aggregation(question)
    dimension = _extract_dimension(question)
    
    parts = []
    if entity_value:
        parts.append(f"e:{entity_value}")
    if agg != "total":
        parts.append(f"a:{agg}")
    if dimension:
        parts.append(f"d:{dimension}")
    
    key = "|".join(parts) if parts else "all"
    return hashlib.md5(key.encode()).hexdigest()


# ---- 信任等级 ----

TRUST_ORDER = {
    "ephemeral": 0,  # 临时：LLM fallback 生成，下次重新验证
    "confirmed": 1,  # 已确认：模板匹配生成
    "verified": 2,   # 已验证：用户点赞/人工审核
}


# ---- 数据库操作 ----


def _get_conn() -> sqlite3.Connection:
    """获取数据库连接"""
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db():
    """初始化查询缓存表"""
    with _lock:
        conn = _get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS query_cache (
                    exact_key TEXT PRIMARY KEY,
                    normalized_key TEXT NOT NULL,
                    semantic_key TEXT NOT NULL,
                    sql_text TEXT NOT NULL,
                    chart_type TEXT DEFAULT 'auto',
                    hits INTEGER DEFAULT 0,
                    first_asked_at TEXT NOT NULL,
                    last_asked_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_norm ON query_cache(normalized_key)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_semantic ON query_cache(semantic_key)")
            conn.commit()
        finally:
            conn.close()


def _init_db_v2():
    """增加 normalized_key 列（向后兼容）"""
    with _lock:
        conn = _get_conn()
        try:
            cols = conn.execute("PRAGMA table_info(query_cache)").fetchall()
            col_names = [c["name"] for c in cols]

            if "normalized_key" not in col_names:
                conn.execute("ALTER TABLE query_cache ADD COLUMN normalized_key TEXT DEFAULT ''")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_norm ON query_cache(normalized_key)")
                print("[Cache v2] 已增加 normalized_key 列")

            if "semantic_key" not in col_names:
                conn.execute("ALTER TABLE query_cache ADD COLUMN semantic_key TEXT DEFAULT ''")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_semantic ON query_cache(semantic_key)")
                print("[Cache v2] 已增加 semantic_key 列")

            conn.commit()
        except Exception as e:
            print(f"[Cache v2] 初始化失败: {e}")
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
                conn.execute("ALTER TABLE query_cache ADD COLUMN trust_level TEXT DEFAULT 'confirmed'")
                conn.execute(
                    "UPDATE query_cache SET trust_level = 'confirmed' WHERE trust_level IS NULL"
                )
                print("[Cache v3] 已增加 trust_level 列")

            conn.commit()
        except Exception as e:
            print(f"[Cache v3] 初始化失败: {e}")
        finally:
            conn.close()


_init_db()
_init_db_v2()
_init_db_v3()


# ---- 查询 ----


def lookup(question: str) -> Optional[dict]:
    """三层缓存查找：精确 → 归一化 → 语义
    
    按优先级依次尝试：
    1. 精确匹配 — 问题完全一致
    2. 归一化匹配 — 去停用词后的核心词相同
    3. 语义匹配 — entity+agg+dimension 组合键相同
    
    返回: {"sql", "chart_type", "layer", "hits"} 或 None
    """
    exact = question.strip()
    norm = normalize(exact)
    sem = extract_semantic_key(exact)

    with _lock:
        conn = _get_conn()
        try:
            # Layer 1: 精确匹配
            row = conn.execute(
                "SELECT sql_text, chart_type, hits FROM query_cache WHERE exact_key = ?",
                (exact,),
            ).fetchone()
            if row:
                return {"sql": row["sql_text"], "chart_type": row["chart_type"], "layer": "exact", "hits": row["hits"]}

            # Layer 2: 归一化匹配
            row = conn.execute(
                "SELECT sql_text, chart_type, hits FROM query_cache WHERE normalized_key = ? ORDER BY hits DESC LIMIT 1",
                (norm,),
            ).fetchone()
            if row:
                return {"sql": row["sql_text"], "chart_type": row["chart_type"], "layer": "normalized", "hits": row["hits"]}

            # Layer 3: 语义匹配
            row = conn.execute(
                "SELECT sql_text, chart_type, hits FROM query_cache WHERE semantic_key = ? ORDER BY hits DESC LIMIT 1",
                (sem,),
            ).fetchone()
            if row:
                return {"sql": row["sql_text"], "chart_type": row["chart_type"], "layer": "semantic", "hits": row["hits"]}

            return None
        finally:
            conn.close()


def lookup_by_intent(intent_cache_key: str, min_trust: str = "ephemeral") -> Optional[dict]:
    """按结构化意图 key 查找缓存
    
    基于 QueryIntent.cache_key 生成的 md5 精确匹配，
    同语义不同表述 → 同一 key
    
    信任等级过滤规则：
    - min_trust="confirmed": 只返回 confirmed 和 verified 的缓存
    - min_trust="verified": 只返回 verified 的缓存
    - min_trust="ephemeral": 返回全部（含临时缓存）
    """
    min_order = TRUST_ORDER.get(min_trust, 1)

    with _lock:
        conn = _get_conn()
        try:
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


def store(
    question: str,
    sql: str,
    chart_type: str = "auto",
) -> bool:
    """写入三层缓存（精确 + 归一化 + 语义）"""
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
                   VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
                (exact, norm, sem, sql, chart_type, now, now),
            )
            conn.commit()
            return True
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


def invalidate_by_question(question: str) -> int:
    """清除指定问题的所有缓存"""
    exact = question.strip()
    with _lock:
        conn = _get_conn()
        try:
            cursor = conn.execute(
                "DELETE FROM query_cache WHERE exact_key = ? OR normalized_key = ? OR semantic_key = ?",
                (exact, normalize(exact), extract_semantic_key(exact)),
            )
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()


def invalidate_by_history_id(history_id: str) -> int:
    """清除指定历史记录的缓存"""
    with _lock:
        conn = _get_conn()
        try:
            cursor = conn.execute(
                "DELETE FROM query_cache WHERE exact_key = ?",
                (history_id,),
            )
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()


def upgrade_trust(question: str, by_intent_key: bool = True) -> bool:
    """升级缓存信任等级为 verified"""
    exact = question.strip()
    with _lock:
        conn = _get_conn()
        try:
            if by_intent_key:
                norm = normalize(exact)
                cursor = conn.execute(
                    "UPDATE query_cache SET trust_level = 'verified', hits = hits + 1 "
                    "WHERE normalized_key = ?",
                    (norm,),
                )
            else:
                cursor = conn.execute(
                    "UPDATE query_cache SET trust_level = 'verified', hits = hits + 1 "
                    "WHERE exact_key = ?",
                    (exact,),
                )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()


def downgrade_trust(question: str, by_intent_key: bool = True) -> bool:
    """降级缓存信任等级为 ephemeral"""
    exact = question.strip()
    with _lock:
        conn = _get_conn()
        try:
            if by_intent_key:
                norm = normalize(exact)
                cursor = conn.execute(
                    "UPDATE query_cache SET trust_level = 'ephemeral' "
                    "WHERE normalized_key = ?",
                    (norm,),
                )
            else:
                cursor = conn.execute(
                    "UPDATE query_cache SET trust_level = 'ephemeral' "
                    "WHERE exact_key = ?",
                    (exact,),
                )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()