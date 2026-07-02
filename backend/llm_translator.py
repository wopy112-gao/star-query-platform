"""星宝语料场景查询系统 — LLM 自然语言→SQL 翻译器（L2）

调用 DeepSeek API 将用户自然语言问题翻译为 DuckDB SQL。

v4 改造内容：
1. Schema 表示从自然语言列表改为 DDL 格式（参考 DAIL-SQL CR 表示法）
2. Few-shot 示例按标签分类，动态选取 2-3 条（不再硬编码 18 条全部注入）
3. 移除硬编码的业务规则/药品/疾病/地域列表（移入后处理层）
4. 统一两条 Prompt 路径的结构（三段式：DDL + 示例 + 任务）
"""

import re
import json
import time
from typing import Optional
from urllib import request as urllib_request
from urllib.error import URLError

from config import settings
from schema_knowledge import SCHEMA_KNOWLEDGE
from domain_knowledge import kb
from intent_schemas import QueryIntent, Aggregation

# 确保领域知识引擎加载
if not kb.is_loaded:
    kb.load()

# ============================================================

from schema_ddl import get_ddl_string


# ============================================================
# DDL 懒加载
# ============================================================


# ============================================================
# 分类示例库
# ============================================================

# 每条示例带标签，用于按意图动态选择
CATEGORIZED_EXAMPLES = {
    "通用统计": [
        {"question": "总场景数", "sql": "SELECT COUNT(DISTINCT 场景ID) AS 总场景数 FROM data"},
        {"question": "联合用药率", "sql": "SELECT ROUND(COUNT(DISTINCT CASE WHEN 是否联合用药='是' THEN 场景ID END) * 100.0 / COUNT(DISTINCT 场景ID), 1) AS 联合用药率 FROM data"},
        {"question": "问症率", "sql": "SELECT ROUND(COUNT(DISTINCT CASE WHEN 是否问症='是' THEN 场景ID END) * 100.0 / COUNT(DISTINCT 场景ID), 1) AS 问症率 FROM data"},
    ],
    "疾病查询": [
        {"question": "感冒场景数", "sql": "SELECT COUNT(DISTINCT 场景ID) AS 场景数 FROM data WHERE 疾病名称 LIKE '%感冒%'"},
        {"question": "疾病TOP10分布", "sql": "SELECT 疾病名称, COUNT(DISTINCT 场景ID) AS 场景数, ROUND(COUNT(DISTINCT 场景ID) * 100.0 / (SELECT COUNT(DISTINCT 场景ID) FROM data), 1) AS 占比 FROM data GROUP BY 疾病名称 ORDER BY 场景数 DESC LIMIT 10"},
    ],
    "药品查询": [
        {"question": "美林在哪些城市出现最多", "sql": "SELECT 城市, COUNT(DISTINCT 场景ID) AS 场景数 FROM data WHERE (顾客点名药品 LIKE '%美林%' OR 场景提及药品 LIKE '%美林%' OR 订单药品 LIKE '%美林%') GROUP BY 城市 ORDER BY 场景数 DESC LIMIT 10"},
        {"question": "诺欣妥场景明细", "sql": "SELECT * FROM data WHERE (顾客点名药品 LIKE '%诺欣妥%' OR 场景提及药品 LIKE '%诺欣妥%' OR 订单药品 LIKE '%诺欣妥%') LIMIT 50"},
    ],
    "成交相关": [
        {"question": "成交率", "sql": "SELECT ROUND(COUNT(DISTINCT CASE WHEN 交易是否达成='是' THEN 场景ID END) * 100.0 / COUNT(DISTINCT 场景ID), 1) AS 成交率 FROM data"},
        {"question": "感冒成交率", "sql": "SELECT ROUND(COUNT(DISTINCT CASE WHEN 交易是否达成='是' THEN 场景ID END) * 100.0 / COUNT(DISTINCT 场景ID), 1) AS 成交率 FROM data WHERE 疾病名称 LIKE '%感冒%'"},
        {"question": "美林成交场景数省份分布", "sql": "SELECT 省份, COUNT(DISTINCT 场景ID) AS 成交场景数 FROM data WHERE 交易是否达成='是' AND (订单药品 LIKE '%美林%') GROUP BY 省份 ORDER BY 成交场景数 DESC"},
    ],
    "地域分布": [
        {"question": "各省份场景数排名", "sql": "SELECT 省份, COUNT(DISTINCT 场景ID) AS 场景数 FROM data GROUP BY 省份 ORDER BY 场景数 DESC"},
        {"question": "高血压各省场景数", "sql": "SELECT 省份, COUNT(DISTINCT 场景ID) AS 场景数 FROM data WHERE 疾病名称 LIKE '%高血压%' GROUP BY 省份 ORDER BY 场景数 DESC"},
    ],
    "时间趋势": [
        {"question": "月度趋势", "sql": "SELECT strftime(ydate, '%Y-%m') AS 月份, COUNT(DISTINCT 场景ID) AS 场景数 FROM data GROUP BY 月份 ORDER BY 月份"},
        {"question": "感冒月度场景数趋势", "sql": "SELECT strftime(ydate, '%Y-%m') AS 月份, COUNT(DISTINCT 场景ID) AS 场景数 FROM data WHERE 疾病名称 LIKE '%感冒%' GROUP BY 月份 ORDER BY 月份"},
    ],
    "时间范围": [
        {"question": "最近7天总场景数", "sql": "SELECT COUNT(DISTINCT 场景ID) AS 总场景数 FROM data WHERE ydate >= CURRENT_DATE - INTERVAL '7' DAY"},
        {"question": "本月各省场景数", "sql": "SELECT 省份, COUNT(DISTINCT 场景ID) AS 场景数 FROM data WHERE strftime(ydate, '%Y-%m') = strftime(CURRENT_DATE, '%Y-%m') GROUP BY 省份 ORDER BY 场景数 DESC"},
        {"question": "2026年各省场景数", "sql": "SELECT 省份, COUNT(DISTINCT 场景ID) AS 场景数 FROM data WHERE strftime(ydate, '%Y') = '2026' GROUP BY 省份 ORDER BY 场景数 DESC"},
    ],
    "实体计数": [
        {"question": "覆盖多少个省份", "sql": "SELECT COUNT(DISTINCT 省份) AS 省份数 FROM data"},
        {"question": "山东省有多少门店", "sql": "SELECT COUNT(DISTINCT 门店ID) AS 门店数 FROM data WHERE 省份='山东省'"},
    ],
}


def _select_examples(intent: Optional[QueryIntent], question: str = "", top_k: int = 2) -> list[dict]:
    """根据意图或问题关键词动态选择最相关的示例

    策略：
    - 有 intent 时，根据条件类型和查询模式匹配分类标签
    - 无 intent 时，根据问题关键词匹配分类标签
    - 返回 top_k 条示例
    """
    if intent:
        # 从 intent 推理标签
        tags = set()
        for c in intent.conditions:
            if c.type == "time_range":
                tags.add("时间范围")
            elif c.type in ("disease",):
                tags.add("疾病查询")
            elif c.type in ("drug_any", "drug_named", "drug_mentioned", "drug_ordered"):
                tags.add("药品查询")
            elif c.type in ("deal_yes", "deal_no"):
                tags.add("成交相关")
            elif c.type == "geo":
                tags.add("地域分布")
        if intent.dimension in ("省份", "城市"):
            tags.add("地域分布")
        if intent.query_pattern == "trend":
            tags.add("时间趋势")
        if intent.agg in ("成交率", "成交场景数", "未成交场景数"):
            tags.add("成交相关")
        if intent.dedup_field:
            tags.add("实体计数")
        if not tags:
            tags.add("通用统计")
    else:
        # 无 intent：根据关键词推理
        tags = set()
        q = question.lower()
        if any(kw in q for kw in ["最近", "近", "本月", "本周", "今年", "昨天"]):
            tags.add("时间范围")
        if any(kw in q for kw in ["趋势", "月度", "月变化"]):
            tags.add("时间趋势")
        if any(kw in q for kw in ["感冒", "咳嗽", "鼻炎", "发烧", "高血压", "疾病"]):
            tags.add("疾病查询")
        if any(kw in q for kw in ["多少门店", "多少店员", "多少药师", "覆盖多少", "有多少", "覆盖了"]):
            tags.add("实体计数")
        if any(kw in q for kw in ["省份", "城市", "分布", "排名"]):
            tags.add("地域分布")
        if any(kw in q for kw in ["成交", "成交率", "售卖"]):
            tags.add("成交相关")
        if not tags:
            tags.add("通用统计")

    # 排序确保稳定性：优先疾病/药品/成交/时间，其次分布/趋势
    tag_priority = ["药品查询", "疾病查询", "成交相关", "时间范围", "时间趋势", "地域分布", "实体计数", "通用统计"]
    tags = sorted(tags, key=lambda t: tag_priority.index(t) if t in tag_priority else 99)

    # 收集匹配标签下的示例
    candidates = []
    seen = set()
    # 优先从每个匹配标签取1条，保证覆盖所有标签
    for tag in tags:
        if tag in CATEGORIZED_EXAMPLES:
            for ex in CATEGORIZED_EXAMPLES[tag]:
                if ex["sql"] not in seen:
                    seen.add(ex["sql"])
                    candidates.append(ex)
                    break  # 每个标签至少1条

    # 补满到 top_k 条（从所有匹配标签中补充）
    if len(candidates) < top_k:
        for tag in tags:
            if tag in CATEGORIZED_EXAMPLES:
                for ex in CATEGORIZED_EXAMPLES[tag]:
                    if ex["sql"] not in seen:
                        seen.add(ex["sql"])
                        candidates.append(ex)
                    if len(candidates) >= top_k:
                        break
            if len(candidates) >= top_k:
                break

    # 如果还不够，补充通用统计
    if len(candidates) < top_k:
        for ex in CATEGORIZED_EXAMPLES["通用统计"]:
            if ex["sql"] not in seen:
                seen.add(ex["sql"])
                candidates.append(ex)
            if len(candidates) >= top_k:
                break

    return candidates[:top_k]


# ============================================================
# Prompt 模板（DDL 驱动版）
# ============================================================

SYSTEM_PROMPT = """你是一个医药零售数据的 SQL 专家。
根据用户问题生成 DuckDB SQL 查询语句。

## 数据库结构
{ddl}

## 关键规则
- 场景数 = COUNT(DISTINCT 场景ID)
- 药品匹配：用 LIKE '%关键词%'，同时查 顾客点名药品/场景提及药品/订单药品
- 时间过滤：用 ydate 字段（格式 YYYY-MM-DD）
- 成交判断：交易是否达成='是'
- 地域过滤：省份='xx省' 或 城市 LIKE '%xx%'
- 成交口径区分：
  - 场景级（不限定药品）：交易是否达成='是'
  - 商品级（限定药品）：交易是否达成='是' AND 订单药品 LIKE '%药品名%'
- 明细查询不加 LIMIT（由前端分页控制）
- 仅输出 SQL，不要额外解释

## 参考示例
{examples}

## 任务
用户问题：{question}
请生成 DuckDB SQL："""


def _build_prompt(question: str) -> list[dict]:
    """构建 LLM 调用消息（三段式：DDL + 示例 + 任务）"""
    ddl = _get_ddl()
    examples = _select_examples(intent=None, question=question, top_k=3)
    examples_section = "\n\n".join([
        f"问：{ex['question']}\nSQL：{ex['sql']}"
        for ex in examples
    ])

    system_prompt = SYSTEM_PROMPT.format(
        ddl=ddl,
        examples=examples_section,
        question=question,
    )

    return [
        {"role": "system", "content": system_prompt},
    ]


# ============================================================
# 精简 Prompt（结构化意图感知版本）
# ============================================================

INTENT_SYSTEM_PROMPT = """你是一个医药零售数据的 SQL 专家。
已分析用户的意图（见下方结构化 JSON），请据此生成 DuckDB SQL。

## 数据库结构
{ddl}

## 关键规则
- 场景数 = COUNT(DISTINCT 场景ID)
- 药品匹配：用 LIKE '%关键词%'
- 成交口径：场景级（交易是否达成='是'）| 商品级（交易是否达成='是' AND 订单药品 CONTAINS '药品名'）
- 明细查询不加 LIMIT（由前端分页控制），分布查询按意图中的 limit 值
- 仅输出 SQL，不要额外解释

## 参考示例
{examples}

## 意图
原始问题：{raw_question}
意图结构化：{intent_json}

请生成 DuckDB SQL："""


def _build_intent_prompt(intent: QueryIntent) -> list[dict]:
    """为结构化意图构建 Prompt（三段式：DDL + 动态示例 + 意图）"""
    ddl = _get_ddl()

    # 动态选择示例
    examples = _select_examples(intent=intent, top_k=2)
    examples_section = "\n\n".join([
        f"近似场景「{ex['question']}」:\n{ex['sql']}"
        for ex in examples
    ])

    system_prompt = INTENT_SYSTEM_PROMPT.format(
        ddl=ddl,
        limit=intent.limit,
        examples=examples_section,
        raw_question=intent.raw_question,
        intent_json=json.dumps(intent.to_dict(), ensure_ascii=False),
    )

    return [
        {"role": "system", "content": system_prompt},
    ]


# ============================================================
# LLM 调用
# ============================================================

def _call_llm(messages: list[dict]) -> Optional[str]:
    """调用 DeepSeek API"""
    api_key = settings.LLM_API_KEY
    if not api_key:
        return None

    url = f"{settings.LLM_BASE_URL.rstrip('/')}/v1/chat/completions"
    payload = json.dumps({
        "model": settings.LLM_MODEL,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 500,
    }).encode("utf-8")

    req = urllib_request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        resp = urllib_request.urlopen(req, timeout=settings.LLM_TIMEOUT_SEC)
        body = json.loads(resp.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"].strip()
        return content
    except URLError as e:
        print(f"[LLM] 请求失败: {e}")
        return None
    except (KeyError, json.JSONDecodeError) as e:
        print(f"[LLM] 解析响应失败: {e}")
        return None


def _extract_sql(llm_response: str) -> Optional[str]:
    """从 LLM 回复中提取 SQL 语句"""
    # 尝试从 ```sql ... ``` 代码块中提取
    sql_blocks = re.findall(r"```(?:sql)?\s*\n?(.*?)\n?```", llm_response, re.DOTALL)
    if sql_blocks:
        sql = sql_blocks[0].strip()
        return sql

    # 尝试直接取第一条 SELECT 语句
    lines = llm_response.strip().split("\n")
    sql_lines = []
    in_sql = False
    for line in lines:
        if re.match(r"^\s*(SELECT|WITH)\s", line, re.IGNORECASE):
            in_sql = True
        if in_sql:
            sql_lines.append(line)
            if line.rstrip().endswith(";"):
                break

    if sql_lines:
        return "\n".join(sql_lines).strip().rstrip(";")

    # 检查错误标记
    if "--ERROR:" in llm_response:
        error_msg = llm_response.split("--ERROR:")[-1].strip()
        return f"--ERROR: {error_msg}"

    return None


def _fix_contradictory_where(sql: str) -> str:
    """修复 LLM 产生的自相矛盾 WHERE 条件。"""
    like_keywords = set()
    for m in re.finditer(r"LIKE\s+'%([^']+)%'", sql, re.IGNORECASE):
        like_keywords.add(m.group(1))

    if not like_keywords:
        return sql

    for m in re.finditer(
        r"AND\s+\w+\s+NOT\s+LIKE\s+'%([^']+)%'", sql, re.IGNORECASE
    ):
        keyword = m.group(1)
        if keyword in like_keywords:
            print(f"[SQL自检] 发现自相矛盾条件: NOT LIKE '%{keyword}%'，已自动移除")
            sql = sql.replace(m.group(0), "")

    sql = re.sub(r"\s{2,}", " ", sql)
    sql = re.sub(r"WHERE\s+AND\s+", "WHERE ", sql)

    return sql.strip()


def _fix_disease_in_to_like(sql: str) -> str:
    """将 WHERE 疾病名称 IN ('糖尿病', '咳嗽') 转换为
       WHERE (疾病名称 LIKE '%糖尿病%' OR 疾病名称 LIKE '%咳嗽%')

    因为实际数据中疾病名称是层级格式（如"内分泌与代谢疾病-2型糖尿病"），
    IN 精确匹配会返回 0 条。
    """
    # 匹配: WHERE 疾病名称 IN ('val1', 'val2', ...)
    # 拆分为 WHERE + 字段名 + IN(...)，避免字段名重复出现在结果中
    pattern = re.compile(
        r"(WHERE\s+)(\w+)\s*IN\s*\(\s*('[^']*'(?:\s*,\s*'[^']*')*)\s*\)",
        re.IGNORECASE | re.DOTALL,
    )
    def _replacer(m: re.Match) -> str:
        prefix = m.group(1)   # "WHERE "
        field = m.group(2)    # "疾病名称"
        values_str = m.group(3)
        # 提取所有单引号值
        values = re.findall(r"'([^']*)'", values_str)
        if not values:
            return m.group(0)
        # 构建 OR 条件
        like_clauses = " OR ".join(
            f"{field} LIKE '%{v}%'" for v in values
        )
        return f"{prefix}({like_clauses})"

    new_sql = pattern.sub(_replacer, sql)
    if new_sql != sql:
        print(f"[SQL修复] 疾病名称 IN → LIKE 模糊匹配")
    return new_sql


# ============================================================
# 对外接口
# ============================================================

def translate_with_intent(intent: QueryIntent) -> dict:
    """
    基于结构化意图的翻译。
    """
    api_key = settings.LLM_API_KEY
    if not api_key:
        return {"success": False, "error": "LLM 翻译尚未配置"}

    messages = _build_intent_prompt(intent)
    start = time.time()

    response = _call_llm(messages)
    elapsed = round((time.time() - start) * 1000, 2)

    if response is None:
        return {"success": False, "error": "LLM 调用失败"}

    sql = _extract_sql(response)
    if sql is not None:
        sql = _fix_contradictory_where(sql)
        sql = _fix_disease_in_to_like(sql)

    if sql is None:
        return {"success": False, "error": f"LLM 返回无法解析: {response[:200]}"}

    if sql.startswith("--ERROR:"):
        return {"success": False, "error": sql.replace("--ERROR:", "").strip()}

    return {"success": True, "sql": sql, "elapsed_ms": elapsed, "llm_response": response}


def translate(question: str, intent: Optional[QueryIntent] = None) -> dict:
    """
    将自然语言问题翻译为 SQL。

    两条路径：
    1. translate("问题", intent=obj) → 意图感知路径（DDL + 动态示例 + 意图JSON）
    2. translate("问题") → fallback 路径（DDL + 动态示例 + 问题文本）
    """
    if intent is not None:
        return translate_with_intent(intent)

    api_key = settings.LLM_API_KEY
    if not api_key:
        return {"success": False, "error": "LLM 翻译尚未配置"}

    messages = _build_prompt(question)
    start = time.time()

    response = _call_llm(messages)
    elapsed = round((time.time() - start) * 1000, 2)

    if response is None:
        return {"success": False, "error": "LLM 调用失败，请检查 API Key 和网络连接"}

    sql = _extract_sql(response)

    if sql is not None:
        sql = _fix_contradictory_where(sql)
        sql = _fix_disease_in_to_like(sql)

    if sql is None:
        return {"success": False, "error": f"LLM 返回无法解析: {response[:200]}"}

    if sql.startswith("--ERROR:"):
        return {"success": False, "error": sql.replace("--ERROR:", "").strip()}

    return {"success": True, "sql": sql, "elapsed_ms": elapsed, "llm_response": response}
