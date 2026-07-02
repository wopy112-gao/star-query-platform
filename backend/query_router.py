"""星宝语料场景查询系统 — 查询路由

v3: 新增 Prompt 拆解 Pipeline
    意图拆解 → 结构化缓存 → 模板匹配(四元组) → LLM精简(bailout) → 质量门禁
    v2 旧路径保留为 fallback
v3.1: 新增明细分页查询 + CSV 全部数据导出
"""

import json as _json
import re as _re
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from auth import get_current_user
from models import (
    QueryRequest,
    SqlQueryRequest,
    ExportRequest,
    QueryResponse,
    QueryInfo,
    QueryResult,
    ChartInfo,
    ConfidenceInfo,
    ExplanationInfo,
    ExplanationItem,
    IntentInfo,
    IntentCondition,
    SchemaResponse,
    ColumnInfo,
    TemplateItem,
    HistoryResponse,
    DeleteHistoryResponse,
    FeedbackRequest,
    FeedbackResponse,
    InsightsResponse,
    TotalStats,
    TrendItem,
    TopItem,
    AlertItem,
)
from sql_engine import engine
from template_matcher import matcher
from sql_renderer import renderer
from chart_builder import chart_builder
from history_store import add_history, get_history, delete_history, clear_history
from schema_knowledge import SCHEMA_KNOWLEDGE
from llm_translator import translate as llm_translate
from query_intent import translate as intent_translate
from insights import InsightCache
from feedback_store import submit_feedback, get_feedback_for_history, delete_feedback
from query_cache import (
    lookup as cache_lookup,
    store as cache_store,
    lookup_by_intent as cache_lookup_by_intent,
    store_with_intent as cache_store_with_intent,
    invalidate_by_question,
    invalidate_by_history_id,
    upgrade_trust,
    downgrade_trust,
)
from data_scope import scope_checker
from sql_validator import (
    validate_dimensions,
    sanity_check_results,
    validate_intent_consistency,
    validate_time_consistency,
)

import re as _re

router = APIRouter(prefix="/api", tags=["查询"])


# ===== ATC Enrich（药品标准化字段补充） =====


def _parse_drug_json(drug_json_str):
    """解析药品 JSON 数组，返回药品名列表"""
    if not drug_json_str or drug_json_str == '[]':
        return []
    try:
        return _json.loads(drug_json_str)
    except (_json.JSONDecodeError, TypeError):
        return []


def _lookup_atc(drug_name, mapping_df):
    """查映射表，返回单个药品的 ATC 信息"""
    if mapping_df is None or mapping_df.empty:
        return None
    match = mapping_df[mapping_df['原始药品名称'] == drug_name]
    if len(match) == 0:
        return None
    row = match.iloc[0]
    return {
        'ATC编码': row.get('ATC编码', ''),
        'ATC第3级': row.get('ATC第3级(药理亚组)', ''),
        'ATC第1级': row.get('ATC第1级(解剖大类)', ''),
        '中西药分类': row.get('中西药分类', ''),
        '置信度': row.get('置信度', ''),
    }


def enrich_query_results(rows):
    """????????? ATC ????????????
    
    ?????????????? dict?O(1) ??
    """
    if not rows:
        return rows

    mapping_df = engine.get_drug_mapping_df()
    if mapping_df.empty:
        return rows

    drug_fields = ['??????', '??????', '????', '??????JSON', '??????JSON']
    atc_suffixes = ['_ATC??', '_ATC??', '_ATC??', '_???', '_???', '_ATC??']

    # ???????? dict
    drug_to_atc = {}
    for _, row in mapping_df.iterrows():
        orig_name = row.get('??????', '')
        if orig_name:
            drug_to_atc[orig_name] = {
                'ATC??': row.get('ATC??', ''),
                'ATC?3?': row.get('ATC?3?(????)', ''),
                'ATC?1?': row.get('ATC?1?(????)', ''),
                '?????': row.get('?????', ''),
                '???': row.get('???', ''),
            }

    enriched = []
    all_atc_cols = set()
    for row in rows:
        for field in drug_fields:
            if field not in row:
                continue
            raw_val = str(row[field]) if row[field] is not None else '[]'
            try:
                drug_names = json.loads(raw_val)
            except (json.JSONDecodeError, TypeError):
                continue
            if not drug_names:
                continue
            for suffix in atc_suffixes:
                all_atc_cols.add(f'{field}{suffix}')

    for row in rows:
        new_row = dict(row)
        for field in drug_fields:
            if field not in row:
                continue
            raw_val = str(row[field]) if row[field] is not None else '[]'
            try:
                drug_names = json.loads(raw_val)
            except (json.JSONDecodeError, TypeError):
                continue
            if not drug_names:
                continue

            primary_drug = drug_names[0]
            atc_info = drug_to_atc.get(primary_drug)
            if atc_info:
                keys = ['ATC??', 'ATC?3?', 'ATC?1?', '?????', '???', 'ATC??']
                for suffix, key in zip(atc_suffixes, keys):
                    new_row[f'{field}{suffix}'] = atc_info.get(key, '')
            else:
                for suffix in atc_suffixes:
                    new_row[f'{field}{suffix}'] = None
        enriched.append(new_row)

    return enriched



def _validate_sql_structure(question: str, sql: str) -> list[str]:
    """SQL 意图-结构交叉验证

    检查用户的自然语言问题与生成的 SQL 结构是否匹配。
    返回发现的问题列表（空 = 验证通过）。
    """
    warnings: list[str] = []
    q = question.strip().lower()
    sql_upper = sql.upper()

    # ---- 检查1：明细 vs 聚合 ----
    if any(w in q for w in ['明细', '详情', '逐条', '罗列']) and 'GROUP BY' in sql_upper:
        warnings.append('您问的是"明细"（逐条数据），但当前查询做了聚合统计（GROUP BY）。如需明细请重新提交。')

    # ---- 检查2：分布 vs 无分组 ----
    if any(w in q for w in ['分布', '排名', '排序', '呈现']) and 'GROUP BY' not in sql_upper:
        warnings.append('您问的是分布/排名，但当前查询没有分组聚合（GROUP BY），可能不会按预期呈现。')

    # ---- 检查3：省份/城市维度 vs GROUP BY 列 ----
    if '按省份' in q or '省分布' in q or '省份分布' in q or '省级分布' in q:
        if 'GROUP BY' not in sql_upper:
            warnings.append('您要求按省份呈现，但查询未做 GROUP BY。')
        else:
            group_by_part = sql.upper().split('GROUP BY')[-1] if 'GROUP BY' in sql_upper else ''
            if '省份' not in group_by_part:
                warnings.append(f'您要求按省份分布展示，但 GROUP BY 的不是省份列（当前: {group_by_part.strip()[:30]}）。')

    if '按城市' in q or '城市分布' in q:
        if 'GROUP BY' not in sql_upper:
            warnings.append('您要求按城市呈现，但查询未做 GROUP BY。')
        else:
            group_by_part = sql.upper().split('GROUP BY')[-1] if 'GROUP BY' in sql_upper else ''
            if '城市' not in group_by_part:
                warnings.append(f'您要求按城市分布展示，但 GROUP BY 的不是城市列。')

    # ---- 检查4：疾病查询没有 WHERE 条件 ----
    disease_detected = False
    for kw in ['高血压', '感冒', '糖尿病', '鼻炎', '咳嗽', '发烧', '哮喘', '咽炎', '支气管']:
        if kw in q:
            disease_detected = True
            break
    if disease_detected and 'WHERE' not in sql_upper and '疾病名称' not in sql_upper:
        warnings.append(f'您提到了疾病名称，但查询中没有看到 WHERE 疾病条件过滤。')

    return warnings


def _rewrite_sql_for_detail(sql: str) -> str:
    """将聚合查询改写为明细查询（去掉 GROUP BY，改为 SELECT *）"""
    # 找到 FROM 子句
    from_match = _re.search(r"(FROM\s+\w+(?:\s+AS\s+\w+)?)", sql, _re.IGNORECASE)
    if not from_match:
        return sql

    # 找到 WHERE 子句（如果有）
    where_match = _re.search(r"(WHERE\s+.*)", sql, _re.IGNORECASE)
    where_part = where_match.group(1).rstrip().rstrip(";") if where_match else ""

    # 构建明细查询
    base = f"SELECT * {from_match.group(1)}"
    if where_part:
        # 去掉 GROUP BY / ORDER BY / LIMIT
        where_clean = _re.split(r"\bGROUP\s+BY\b", where_part, flags=_re.IGNORECASE)[0]
        where_clean = _re.split(r"\bORDER\s+BY\b", where_clean, flags=_re.IGNORECASE)[0]
        where_clean = _re.split(r"\bLIMIT\b", where_clean, flags=_re.IGNORECASE)[0]
        base = f"{base} {where_clean}"

    return base


# ===== 分页执行 =====


def _execute_paginated(sql: str, page: int, page_size: int) -> dict:
    """对明细 SQL 执行分页查询

    流程：
    1. 去掉 SQL 中原有的 LIMIT（引擎自动加的那个）
    2. 用子查询包装执行 COUNT
    3. 用子查询包装执行 LIMIT/OFFSET + ORDER BY 场景ID
    返回: {rows, total_count, total_pages, has_prev, has_next}
    """
    # 去掉末尾的 LIMIT（引擎自动加上的）
    sql_clean = _re.sub(
        r"\s*LIMIT\s+\d+(?:\s+OFFSET\s+\d+)?\s*$", "",
        sql.strip(), flags=_re.IGNORECASE
    )

    # 1. COUNT 总数
    count_sql = f"SELECT COUNT(*) AS _cnt FROM ({sql_clean}) AS _sub"
    count_res = engine.execute(count_sql)
    total_count = count_res["rows"][0]["_cnt"] if count_res["rows"] else 0

    # 2. 分页查询
    offset = (page - 1) * page_size
    page_sql = (
        f"SELECT * FROM ({sql_clean}) AS _sub "
        f"ORDER BY 场景ID LIMIT {page_size} OFFSET {offset}"
    )
    page_res = engine.execute(page_sql)
    rows = page_res["rows"]
    elapsed = page_res["elapsed_ms"]

    # 3. 分页信息
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    pagination = {
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page * page_size < total_count,
    }

    return {
        "rows": rows,
        "elapsed_ms": elapsed,
        "total_count": total_count,
        "pagination": pagination,
        "sql_used": page_sql,
    }


# ===== 原始函数 =====


def _compute_confidence(rows: list[dict], total_rows: int) -> ConfidenceInfo:
    """计算数据置信度"""
    if total_rows >= 1000:
        return ConfidenceInfo(level="high", note="样本量充足，数据口径准确")
    elif total_rows >= 100:
        return ConfidenceInfo(level="medium", note="样本量适中，结论仅供参考")
    else:
        return ConfidenceInfo(level="low", note="样本量较小，结论需谨慎使用")


def _build_explanation(sql: str, question: str, source: str) -> ExplanationInfo:
    """根据 SQL 和问题生成算法说明"""
    sql_upper = sql.upper()
    notes = []

    # 1. 统计口径
    if "COUNT(DISTINCT 场景ID)" in sql_upper:
        notes.append(ExplanationItem(
            label="统计口径",
            content="场景数 = 按场景ID去重计数，每个场景ID代表一次独立购药行为"
        ))
    elif "COUNT(*)" in sql_upper or "COUNT(1)" in sql_upper:
        notes.append(ExplanationItem(
            label="统计口径",
            content="行数 = 原始数据行数计数（含同一场景的多条记录）"
        ))

    # 2. 成交条件
    if "交易是否达成" in sql:
        if "订单药品" in sql and "LIKE" in sql_upper:
            notes.append(ExplanationItem(
                label="成交口径",
                content='【商品级成交】交易是否达成=是 且 订单药品包含目标药品。原因：同一场景ID有多行，仅靠交易是否达成不能确认售出的是目标药品'
            ))
        else:
            notes.append(ExplanationItem(
                label="成交口径",
                content='【场景级成交】交易是否达成=是（按疾病/地域/时间维度统计整体成交情况）'
            ))

    # 3. 药品匹配范围
    drug_sources = []
    if "顾客点名药品" in sql:
        drug_sources.append("顾客点名药品（顾客进店后点名购买的药品）")
    if "场景提及药品" in sql:
        drug_sources.append("场景提及药品（整个场景中被提及的药品）")
    if "订单药品" in sql:
        drug_sources.append("订单药品（实际达成交易的药品）")
    if drug_sources:
        notes.append(ExplanationItem(
            label="药品匹配范围",
            content="、".join(drug_sources)
        ))

    # 4. 问症/联合用药/关键信息
    if "是否问症" in sql:
        notes.append(ExplanationItem(
            label="问症率口径",
            content="问症率 = 问症场景数（是否问症='是'）÷ 总场景数 × 100%"
        ))
    if "是否联合用药" in sql:
        notes.append(ExplanationItem(
            label="联合用药率口径",
            content="联合用药率 = 联合用药场景数（是否联合用药='是'）÷ 总场景数 × 100%"
        ))
    if "是否关键信息到达" in sql:
        notes.append(ExplanationItem(
            label="关键信息到达率口径",
            content="关键信息到达率 = 信息到达场景数（是否关键信息到达='是'）÷ 总场景数 × 100%"
        ))

    # 5. 疾病筛选
    like_matches = []
    for m in _re.finditer(r"LIKE\s+'%([^']+)%'", sql, _re.IGNORECASE):
        like_matches.append(m.group(1))
    if like_matches:
        notes.append(ExplanationItem(
            label="疾病筛选",
            content=f"疾病名称包含「{'」「'.join(like_matches)}」"
        ))

    # 6. 时间维度
    if "YDATE" in sql_upper:
        if "SUBSTR" in sql_upper:
            notes.append(ExplanationItem(
                label="时间维度",
                content="按 ydate 字段按月聚合（格式：YYYY-MM）"
            ))
        else:
            notes.append(ExplanationItem(
                label="时间维度",
                content="按 ydate 字段（场景解析日期）过滤"
            ))

    # 7. 地域维度
    if "GROUP BY" in sql_upper:
        group_cols = []
        for m in _re.finditer(r"GROUP\s+BY\s+(\w+)", sql, _re.IGNORECASE):
            group_cols.append(m.group(1))
        if "省份" in group_cols:
            notes.append(ExplanationItem(label="地域维度", content="按省份聚合统计"))
        elif "城市" in group_cols:
            notes.append(ExplanationItem(label="地域维度", content="按城市聚合统计"))

    # 8. 占比/率计算
    if "100.0" in sql or "100.0 /" in sql:
        notes.append(ExplanationItem(
            label="占比计算",
            content="占比 = 子集场景数 ÷ 全集场景数 × 100%（分子分母均为场景ID去重）"
        ))

    # 9. 数据源
    notes.append(ExplanationItem(
        label="数据源",
        content=f"星宝语料场景数据库（{SCHEMA_KNOWLEDGE['total_rows']}行 × {len(SCHEMA_KNOWLEDGE['columns'])}列）"
    ))

    # 10. 来源标注
    notes.append(ExplanationItem(
        label="SQL来源",
        content="预置模板匹配" if source == "template" else "Moss翻译"
    ))

    return ExplanationInfo(sql=sql, notes=notes)


def _build_summary(rows: list[dict]) -> str:
    """生成结果摘要（显示总计，不显示 TOP1）"""
    if not rows:
        return "无匹配数据"

    top = rows[0]
    keys = list(top.keys())

    # 单值结果（1 行 1 列）→ 直接显示值
    if len(rows) == 1 and len(keys) == 1:
        val = top[keys[0]]
        if isinstance(val, int):
            return f"结果: {val:,}"
        elif isinstance(val, float):
            return f"结果: {val:,.1f}"
        return f"结果: {val}"

    # 多列 → 分离分类列和数值列
    cat_keys = []
    num_keys = []
    for k in keys:
        v = top[k]
        if isinstance(v, (int, float)):
            num_keys.append(k)
        else:
            cat_keys.append(k)

    # 有数值列 → 计算总计
    if num_keys:
        # 过滤掉 ID 类字段（场景ID、店员ID 等），它们不是聚合值
        agg_num_keys = [nk for nk in num_keys if "ID" not in nk.upper() and "id" not in nk]

        # 如果过滤后没有真正的聚合数值列 → 当作明细
        if not agg_num_keys:
            return f"共 {len(rows)} 条结果"

        # 优先选非比例类的数值列（排除"率""比""占比"结尾的列）
        primary_num = agg_num_keys[0]  # 默认第一个
        for nk in agg_num_keys:
            if not any(nk.endswith(suffix) for suffix in ("率", "比", "占比")):
                primary_num = nk
                break

        total = sum(
            row.get(primary_num, 0) or 0
            for row in rows
            if isinstance(row.get(primary_num), (int, float))
        )

        # 单行多列 → 显示第一个关键值
        if len(rows) == 1:
            return f"{primary_num}: {total:,}"

        # 多行 → 显示行数 + 总计
        return f"共 {len(rows)} 条 | {primary_num} 总计: {total:,}"

    # 无数值列 → 纯明细
    return f"共 {len(rows)} 条结果"


@router.post("/query", response_model=QueryResponse)
def query(req: QueryRequest, username: str = Depends(get_current_user)):
    """自然语言查询（v3: 意图拆解→结构化缓存→模板→LLM精简→门禁）"""
    question = req.question.strip()

    # ---- (P0) 数据范围预检 ----
    scope_hint = None
    if not scope_checker.is_in_scope(question):
        scope_hint = scope_checker.get_hint(question)
        print(f"[DataScope] 预检: 问题「{question}」可能超出数据范围")
        if scope_hint:
            print(f"[DataScope] → 提示: {scope_hint[:60]}...")

    quality_warnings: list[str] = []

    # ================================================================
    # 阶段1: 意图拆解 + 结构化缓存 + 模板匹配（新 Pipeline）
    # ================================================================
    intent_result = intent_translate(question)
    if intent_result.success and intent_result.intent:
        intent = intent_result.intent
        intent_key = intent.cache_key

        # 阶段1a: 结构化缓存命中
        cache_hit = cache_lookup_by_intent(intent_key)
        if cache_hit:
            sql = cache_hit["sql"]
            chart_type = cache_hit["chart_type"]
            source = "cache"
            print(f"[路由] 结构化缓存命中: {intent.to_dict()}")

        else:
            # 阶段1b: 模板匹配
            matched = matcher.match_by_intent(intent)
            if matched and matched["score"] >= 40:
                template_obj = matched["template_obj"]
                sql = renderer.render(template_obj, intent)

                # 方案B：模板渲染触发 LLM fallback → 走 LLM 生成
                if sql == "__LLM_FALLBACK__":
                    print(f"[路由] 模板渲染触发 LLM fallback (未知条件类型)")
                    trans_result = llm_translate(question, intent=intent)
                    if not trans_result["success"]:
                        return _query_fallback(question, scope_hint, username, req.page, req.page_size)
                    sql = trans_result["sql"]
                    chart_type = trans_result.get("chart_type", "auto")
                    source = "llm_intent"
                else:
                    chart_type = template_obj.get("chart_type", "auto")
                    source = "template"
                    print(f"[路由] 模板匹配: {matched['template_id']} (score={matched['score']})")

            else:
                # 阶段1c: LLM 精简翻译（兜底）
                trans_result = llm_translate(question, intent=intent)
                if not trans_result["success"]:
                    # LLM 兜底失败 → 回退到旧流程
                    print(f"[路由] LLM精简翻译失败，回退旧路径")
                    return _query_fallback(question, scope_hint, username, req.page, req.page_size)

                sql = trans_result["sql"]
                chart_type = trans_result.get("chart_type", "auto")
                source = "llm_intent"
                print(f"[路由] LLM精简翻译生成 SQL")

        # 阶段1d: SQL 质量门禁（意图一致性校验 + 自动恢复）
        if intent and sql:
            intent_warnings = validate_intent_consistency(intent, sql)
            if intent_warnings:
                print(f"[意图校验] 问题「{question}」不一致: {'; '.join(intent_warnings)}")

                # ---- 自动恢复：校验不通过 → 换路径重试 ----
                if source == "template":
                    # 模板匹配的 SQL 有问题 → 尝试 LLM 兜底
                    print(f"[恢复] 模板匹配校验不通过，尝试 LLM 替代...")
                    trans_result = llm_translate(question, intent=intent)
                    if trans_result["success"]:
                        retry_sql = trans_result["sql"]
                        retry_warnings = validate_intent_consistency(intent, retry_sql)
                        if not retry_warnings:
                            print(f"[恢复] ✓ LLM 替代通过，使用 LLM 生成的 SQL")
                            sql = retry_sql
                            chart_type = trans_result.get("chart_type", "auto")
                            source = "template_recovered"
                            quality_warnings = []
                        else:
                            print(f"[恢复] LLM 替代也未通过校验: {'; '.join(retry_warnings)}")
                            quality_warnings.extend(intent_warnings)
                    else:
                        print(f"[恢复] LLM 调用失败，保持原模板结果")
                        quality_warnings.extend(intent_warnings)

                elif source == "llm_intent":
                    # LLM 生成的 SQL 有问题 → 加修正提示重试
                    print(f"[恢复] LLM 翻译校验不通过，尝试重新翻译...")
                    trans_result = llm_translate(question, intent=intent)
                    if trans_result["success"]:
                        retry_sql = trans_result["sql"]
                        retry_warnings = validate_intent_consistency(intent, retry_sql)
                        if not retry_warnings:
                            print(f"[恢复] ✓ LLM 重试通过")
                            sql = retry_sql
                            chart_type = trans_result.get("chart_type", "auto")
                            source = "llm_recovered"
                            quality_warnings = []
                        else:
                            print(f"[恢复] LLM 重试仍未通过: {'; '.join(retry_warnings)}")
                            quality_warnings.extend(intent_warnings)
                    else:
                        print(f"[恢复] LLM 重试调用失败")
                        quality_warnings.extend(intent_warnings)

                else:
                    # cache 或其他来源 → 直接报 warning
                    quality_warnings.extend(intent_warnings)
            # 无 warning，一切正常

        # 阶段1e: 缓存写入（带信任等级）
        if intent and sql and source not in ("cache",):
            try:
                # 模板匹配 → confirmed（信任）
                # LLM fallback → ephemeral（临时的，下次重新验证）
                trust_level = "confirmed" if source in ("template",) else "ephemeral"
                cache_store_with_intent(question, sql, intent.cache_key, chart_type, trust_level)
            except Exception as e:
                print(f"[缓存] 结构化缓存写入失败: {e}")

    else:
        # 意图拆解失败 → 回退到旧流程（v2）
        print(f"[路由] 意图拆解失败: {intent_result.error}，回退旧路径")
        return _query_fallback(question, scope_hint, username, req.page, req.page_size)

    # ================================================================
    # 阶段2: 执行 SQL（带分页支持）
    # ================================================================
    if not sql or sql.startswith("--ERROR:"):
        add_history(question, sql or "", 0, success=False, username=username)
        return QueryResponse(
            success=False,
            error=sql.replace("--ERROR:", "").strip() if sql else "无法生成 SQL",
        )

    # ---- 分页判断：page 有值 且 非聚合查询（无 GROUP BY / 聚合函数）→ 启用分页 ----
    use_pagination = (
        req.page is not None
        and "GROUP BY" not in sql.upper()
        and "COUNT" not in sql.upper()
        and "AVG(" not in sql.upper()
        and "SUM(" not in sql.upper()
        and "MIN(" not in sql.upper()
        and "MAX(" not in sql.upper()
    )
    pagination_info = None

    if use_pagination:
        page_result = _execute_paginated(sql, req.page, req.page_size)
        rows = page_result["rows"]
        # ATC Enrich：补充药品标准化字段
        rows = enrich_query_results(rows)
        elapsed = page_result["elapsed_ms"]
        pagination_info = page_result["pagination"]
        sql = page_result["sql_used"]  # 更新为实际执行的 SQL（含 LIMIT/OFFSET）
        total_count = page_result["total_count"]
        print(f"[分页] 问题「{question}」page={req.page}, total={total_count}, rows={len(rows)}")
    else:
        result = engine.execute(sql)

        if not result["success"]:
            add_history(question, sql, 0, success=False, username=username)
            return QueryResponse(
                success=False,
                error=result["error"],
            )

        rows = result["rows"]
        # ATC Enrich：补充药品标准化字段
        rows = enrich_query_results(rows)
        elapsed = result["elapsed_ms"]

    # ---- 空结果检测 ----
    is_empty = not rows or (
        len(rows) == 1
        and all(
            v is None or (isinstance(v, (int, float)) and v == 0)
            for v in rows[0].values()
        )
    )
    if is_empty:
        print(f"[SQL验证] 问题「{question}」查询结果为空，SQL: {sql[:100]}...")
        if not scope_hint:
            scope_hint = scope_checker.get_hint(question)
            if scope_hint:
                print(f"[DataScope] 问题「{question}」→ 数据范围提示")

    # ================================================================
    # 阶段3: 构建响应
    # ================================================================
    qr = QueryResult(
        summary=_build_summary(rows) if not pagination_info else f"共 {pagination_info['total_count']} 条结果",
        rows=rows,
        total_rows=len(rows),
        row_limit=500,
        truncated=False,
        pagination=pagination_info,
    )

    confidence = _compute_confidence(rows, engine.row_count)

    if chart_type == "table_only" or not rows:
        chart_info = ChartInfo(type="table_only", option=None)
    else:
        chart_built = chart_builder.build(rows)
        chart_info = ChartInfo(
            type=chart_built["type"],
            option=chart_built["option"],
        )

    qi = QueryInfo(
        natural=question,
        sql=sql,
        source=source,
        elapsed_ms=elapsed,
    )

    explanation = _build_explanation(sql, question, source)

    sanity_warnings = sanity_check_results(sql, rows)
    if sanity_warnings:
        print(f"[合理性校验] 问题「{question}」数据异常: {'; '.join(sanity_warnings)}")
        quality_warnings.extend(sanity_warnings)

    # ---- 时间一致性校验（v4）：检测遗漏的时间条件 ----
    time_warnings = validate_time_consistency(question, sql)
    if time_warnings:
        print(f"[时间校验] 问题「{question}」时间条件缺失: {'; '.join(time_warnings)}")
        quality_warnings.extend(time_warnings)

    # ---- 交叉验证（P3）：锚点查询对比 ----
    if rows and intent_result.success and intent_result.intent and source not in ("cache",):
        try:
            from cross_validator import validate_with_anchor
            intent_dict = intent_result.intent.to_dict() if hasattr(intent_result.intent, 'to_dict') else None
            cross_warnings = validate_with_anchor(
                question=question,
                intent=intent_dict,
                sql=sql,
                rows=rows,
                engine=engine,
            )
            if cross_warnings:
                print(f"[交叉验证] 问题「{question}」结果异常: {'; '.join(cross_warnings)}")
                quality_warnings.extend(cross_warnings)
        except ImportError:
            pass  # cross_validator 未安装，跳过
        except Exception as e:
            print(f"[交叉验证] 执行失败: {e}")

    # ---- 校验失败 → 写入反馈事件 ----
    if quality_warnings and intent_result.success and intent_result.intent:
        from incident_writer import write_incident
        write_incident(
            inc_type="validation_fail",
            question=question,
            sql=sql,
            warnings=quality_warnings,
            intent_info=intent_result.intent.to_dict(),
        )

    add_history(question, sql, elapsed, success=True, username=username)

    # 构建意图卡片信息（供前端展示）
    if intent_result.success and intent_result.intent:
        intent_info = IntentInfo(
            query_pattern=intent_result.intent.query_pattern,
            agg=intent_result.intent.agg,
            conditions=[IntentCondition(type=c.type, value=c.value, relation=c.relation)
                         for c in intent_result.intent.conditions],
            dimension=intent_result.intent.dimension,
            route_source=source,
        )
    else:
        intent_info = None

    return QueryResponse(
        success=True,
        query=qi,
        explanation=explanation,
        result=qr,
        chart=chart_info,
        confidence=confidence,
        hint=scope_hint,
        warnings=quality_warnings,
        intent_info=intent_info,
    )


# ===== CSV 导出（全部数据，不限分页） =====


@router.post("/query/export")
def export_csv(req: ExportRequest, username: str = Depends(get_current_user)):
    """根据自然语言问题导出全部数据为 CSV（不限分页）"""
    question = req.question.strip()

    # 复用 query 流程生成 SQL（不分页）
    # 用 QueryRequest（带 page 字段）而不是 ExportRequest
    temp_req = QueryRequest(question=req.question)
    resp = query(temp_req, username)
    if not resp.success:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": f"查询失败: {resp.error}"},
        )

    # 从 response 中获取 SQL（去掉末尾 LIMIT）
    sql = resp.query.sql if resp.query else ""
    if not sql:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "无法获取 SQL"},
        )

    sql_clean = _re.sub(
        r"\s*LIMIT\s+\d+(?:\s+OFFSET\s+\d+)?\s*$", "",
        sql.strip(), flags=_re.IGNORECASE
    )

    # DuckDB COPY → 直接写入 CSV 文件
    out_path = f"/tmp/star_export_{uuid.uuid4().hex}.csv"
    try:
        engine.conn.execute(
            f"COPY ({sql_clean}) TO '{out_path}' (HEADER, DELIMITER ',')"
        )

        # 追加 UTF-8 BOM，确保 Windows Excel 正确识别中文编码
        with open(out_path, 'rb') as f:
            raw = f.read()
        with open(out_path, 'wb') as f:
            f.write(b'\xef\xbb\xbf')
            f.write(raw)

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"CSV 导出失败: {str(e)}"},
        )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return FileResponse(
        out_path,
        media_type="text/csv",
        filename=f"star_query_{ts}.csv",
    )


def _query_fallback(
    question: str,
    scope_hint: Optional[str],
    username: str,
    page: Optional[int] = None,
    page_size: int = 50,
) -> QueryResponse:
    """回退到 v2 旧路径（意图拆解失败或 LLM 精简翻译失败时调用）"""
    quality_warnings: list[str] = []
    pagination_info = None

    # 阶段1: 精确模板匹配（旧）
    matched = matcher.match(question)
    if matched and matched.get("template_id") not in ("t_disease", "t_city", "t9"):
        sql = matched["sql"]
        chart_type = matched["chart_type"]
        source = "template"
    else:
        # 阶段2: 缓存查询（旧）
        cache_hit = cache_lookup(question)
        if cache_hit:
            sql = cache_hit["sql"]
            chart_type = cache_hit["chart_type"]
            source = "cache"
        else:
            # 阶段3: LLM 翻译（全量 Prompt）
            trans_result = llm_translate(question)
            if not trans_result["success"]:
                add_history(question, "", 0, success=False, username=username)
                return QueryResponse(
                    success=False,
                    error=trans_result["error"],
                )
            sql = trans_result["sql"]
            chart_type = trans_result.get("chart_type", "auto")
            source = "moss"

            # ---- 交叉验证（旧） ----
            warnings = _validate_sql_structure(question, sql)
            if warnings:
                print(f"[SQL验证(回退)] 交叉验证: {'; '.join(warnings)}")
                quality_warnings.extend(warnings)
                if any('明细' in w for w in warnings):
                    rewritten = _rewrite_sql_for_detail(sql)
                    if rewritten != sql:
                        print(f"[SQL验证(回退)] 自动改写: 明细查询")
                        sql = rewritten
                        chart_type = "auto"

            try:
                cache_store(question, sql, "auto")
            except Exception:
                pass

    # ---- SQL 质量门禁：维度校验 ----
    dim_warnings = validate_dimensions(question, sql)
    if dim_warnings:
        print(f"[维度校验(回退)] 问题「{question}」维度不匹配: {'; '.join(dim_warnings)}")
        quality_warnings.extend(dim_warnings)

    # 执行查询（带分页支持）
    if not sql or sql.startswith("--ERROR:"):
        add_history(question, sql or "", 0, success=False, username=username)
        return QueryResponse(
            success=False,
            error=sql.replace("--ERROR:", "").strip() if sql else "无法生成 SQL",
        )

    # ---- 分页判断 ----
    use_pagination = (
        page is not None
        and "GROUP BY" not in sql.upper()
        and "COUNT" not in sql.upper()
        and "AVG(" not in sql.upper()
        and "SUM(" not in sql.upper()
        and "MIN(" not in sql.upper()
        and "MAX(" not in sql.upper()
    )

    if use_pagination:
        page_result = _execute_paginated(sql, page, page_size)
        rows = page_result["rows"]
        # ATC Enrich：补充药品标准化字段
        rows = enrich_query_results(rows)
        elapsed = page_result["elapsed_ms"]
        pagination_info = page_result["pagination"]
        sql = page_result["sql_used"]
        print(f"[分页(回退)] 问题「{question}」page={page}, total={pagination_info['total_count']}, rows={len(rows)}")
    else:
        result = engine.execute(sql)

        if not result["success"]:
            add_history(question, sql, 0, success=False, username=username)
            return QueryResponse(
                success=False,
                error=result["error"],
            )

        rows = result["rows"]
        # ATC Enrich：补充药品标准化字段
        rows = enrich_query_results(rows)
        elapsed = result["elapsed_ms"]

    is_empty = not rows or (
        len(rows) == 1
        and all(
            v is None or (isinstance(v, (int, float)) and v == 0)
            for v in rows[0].values()
        )
    )
    if is_empty and not scope_hint:
        scope_hint = scope_checker.get_hint(question)
        if scope_hint:
            print(f"[DataScope(回退)] 空结果 → 范围提示")

    qr = QueryResult(
        summary=_build_summary(rows) if not pagination_info else f"共 {pagination_info['total_count']} 条结果",
        rows=rows,
        total_rows=len(rows),
        row_limit=500,
        truncated=False,
        pagination=pagination_info,
    )

    confidence = _compute_confidence(rows, engine.row_count)

    if chart_type == "table_only" or not rows:
        chart_info = ChartInfo(type="table_only", option=None)
    else:
        chart_built = chart_builder.build(rows)
        chart_info = ChartInfo(
            type=chart_built["type"],
            option=chart_built["option"],
        )

    qi = QueryInfo(
        natural=question,
        sql=sql,
        source=source,
        elapsed_ms=elapsed,
    )

    explanation = _build_explanation(sql, question, source)

    sanity_warnings = sanity_check_results(sql, rows)
    if sanity_warnings:
        quality_warnings.extend(sanity_warnings)

    # ---- 时间一致性校验（v4）----
    time_warnings = validate_time_consistency(question, sql)
    if time_warnings:
        print(f"[时间校验] 问题「{question}」时间条件缺失: {'; '.join(time_warnings)}")
        quality_warnings.extend(time_warnings)

    add_history(question, sql, elapsed, success=True, username=username)

    # 回退路径没有意图拆解结果，intent_info 为空
    intent_info = None

    return QueryResponse(
        success=True,
        query=qi,
        explanation=explanation,
        result=qr,
        chart=chart_info,
        confidence=confidence,
        hint=scope_hint,
        warnings=quality_warnings,
        intent_info=intent_info,
    )


@router.post("/query/sql", response_model=QueryResponse)
def query_sql(req: SqlQueryRequest, username: str = Depends(get_current_user)):
    """SQL 直接查询

    v2: 支持 optional original_question 参数，用户编辑 SQL 后更新缓存
    """
    # ---- 如果用户是在编辑历史查询，先清除旧缓存 ----
    if req.original_question:
        deleted = invalidate_by_question(req.original_question)
        if deleted > 0:
            print(f"[缓存] 用户编辑 SQL，已清除 {deleted} 条相关缓存")

    result = engine.execute(req.sql)

    if not result["success"]:
        add_history(req.sql, req.sql, 0, success=False, username=username)
        return QueryResponse(success=False, error=result["error"])

    rows = result["rows"]
    # ATC Enrich：补充药品标准化字段
    rows = enrich_query_results(rows)
    elapsed = result["elapsed_ms"]

    # ---- 数据范围提示（空结果时） ----
    scope_hint = None
    if not rows:
        hint_question = req.original_question or req.sql
        scope_hint = scope_checker.get_hint(hint_question)
        if scope_hint:
            print(f"[DataScope] SQL查询空结果 → 范围提示: {hint_question[:60]}...")

    # ---- 如果用户是在编辑历史查询，写入新缓存 ----
    if req.original_question:
        try:
            cache_store(req.original_question, req.sql, "auto")
            print(f"[缓存] 已用编辑后的 SQL 更新缓存（原始问题: {req.original_question[:50]}...）")
        except Exception as e:
            print(f"[缓存] 更新缓存失败: {e}")

    qr = QueryResult(
        summary=_build_summary(rows),
        rows=rows,
        total_rows=len(rows),
        row_limit=500,
        truncated=False,
    )

    confidence = _compute_confidence(rows, engine.row_count)

    chart_built = chart_builder.build(rows)
    chart_info = ChartInfo(
        type=chart_built["type"],
        option=chart_built["option"],
    )

    qi = QueryInfo(
        natural=req.sql,
        sql=req.sql,
        source="moss",
        elapsed_ms=elapsed,
    )

    explanation = _build_explanation(req.sql, req.sql, "moss")

    add_history(req.sql, req.sql, elapsed, success=True, username=username)

    # ---- SQL 质量门禁：结果合理性校验 ----
    quality_warnings = sanity_check_results(req.sql, rows)
    if quality_warnings:
        print(f"[合理性校验] SQL查询数据异常: {'; '.join(quality_warnings)}")

    # ---- 时间一致性校验（v4）----
    time_warnings = validate_time_consistency(req.original_question or req.sql, req.sql)
    if time_warnings:
        print(f"[时间校验] 时间条件缺失: {'; '.join(time_warnings)}")
        quality_warnings.extend(time_warnings)

    return QueryResponse(
        success=True,
        query=qi,
        explanation=explanation,
        result=qr,
        chart=chart_info,
        confidence=confidence,
        hint=scope_hint,
        warnings=quality_warnings,
    )


@router.get("/schema", response_model=SchemaResponse)
def get_schema(username: str = Depends(get_current_user)):
    """获取表 Schema"""
    info = engine.get_schema()
    columns = []
    for col in info["columns"]:
        desc = ""
        for kcol in SCHEMA_KNOWLEDGE["columns"]:
            if kcol["name"] == col["name"]:
                desc = kcol["description"]
                break
        columns.append(ColumnInfo(
            name=col["name"],
            dtype=col["dtype"],
            description=desc,
            sample=col["sample"],
        ))
    return SchemaResponse(
        table_name="data",
        total_rows=info["total_rows"],
        columns=columns,
    )


@router.get("/templates", response_model=list[TemplateItem])
def get_templates(username: str = Depends(get_current_user)):
    """获取预置模板列表"""
    templates = matcher.get_all_templates()
    return [TemplateItem(**t) for t in templates]


@router.get("/history", response_model=HistoryResponse)
def query_history(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    keyword: str = Query("", description="搜索关键词"),
    username: str = Depends(get_current_user),
):
    """获取历史查询记录"""
    return get_history(
        username=username,
        page=page,
        limit=limit,
        keyword=keyword if keyword else None,
    )


@router.delete("/history/{history_id}", response_model=DeleteHistoryResponse)
def delete_history_item(
    history_id: str,
    username: str = Depends(get_current_user),
):
    """删除单条历史记录"""
    deleted = delete_history(history_id, username)
    if not deleted:
        return DeleteHistoryResponse(success=False, deleted=0)
    return DeleteHistoryResponse(success=True, deleted=1)


@router.delete("/history", response_model=DeleteHistoryResponse)
def clear_history_all(
    username: str = Depends(get_current_user),
):
    """清空当前用户全部历史记录"""
    count = clear_history(username)
    return DeleteHistoryResponse(success=True, deleted=count)


# ===== 数据洞察 =====

@router.get("/insights", response_model=InsightsResponse)
def get_insights(username: str = Depends(get_current_user)):
    """获取数据看板核心指标"""
    data = InsightCache.get()

    return InsightsResponse(
        total=TotalStats(
            total_scenes=data["total"]["total_scenes"],
            today_scenes=data["total"]["today_scenes"],
            week_scenes=data["total"]["week_scenes"],
            close_rate=data["total"]["close_rate"],
            inquiry_rate=data["total"]["inquiry_rate"],
            combo_rate=data["total"]["combo_rate"],
        ),
        trend=[TrendItem(**t) for t in data["trend"]],
        top_diseases=[TopItem(**t) for t in data["top_diseases"]],
        top_provinces=[TopItem(**t) for t in data["top_provinces"]],
        alerts=[AlertItem(**a) for a in data["alerts"]],
        date_range=data["date_range"],
    )


# ===== 反馈机制 =====

@router.post("/feedback", response_model=FeedbackResponse)
def post_feedback(
    req: FeedbackRequest,
    username: str = Depends(get_current_user),
):
    """提交查询结果反馈（赞/踩）

    v2: 踩自动清除对应缓存
    """
    result = submit_feedback(
        history_id=req.history_id,
        username=username,
        question=req.question,
        sentiment=req.sentiment,
        comment=req.comment,
    )

    # ---- 反馈 → 缓存信任等级调整 ----
    message = "反馈已记录"
    if req.sentiment == "dislike":
        # 踩 → 降级为 ephemeral（下次重新验证）
        downgraded = downgrade_trust(req.question, by_intent_key=False)
        if downgraded:
            message += "，缓存已降级为临时（下次重新验证）"
            print(f"[缓存] 用户踩 → 降级: {req.question[:40]}")
        else:
            # 降级失败（可能无缓存），按旧逻辑清除
            deleted = invalidate_by_question(req.question)
            if deleted > 0:
                msg_extra = f"，已清除 {deleted} 条相关缓存"
                message += msg_extra
                print(f"[缓存] 用户踩了查询「{req.question[:40]}」，已清除 {deleted} 条缓存")

        from incident_writer import write_incident
        write_incident(
            inc_type="user_dislike",
            question=req.question,
            history_id=req.history_id,
            feedback_comment=req.comment,
        )

    elif req.sentiment == "like":
        # 赞 → 升级为 verified（持久缓存，跨 session 可用）
        upgraded = upgrade_trust(req.question, by_intent_key=False)
        if upgraded:
            message += "，缓存已升级为已验证（永久可用）"
            print(f"[缓存] 用户赞 → 升级: {req.question[:40]}")

    return FeedbackResponse(
        success=True,
        id=result["id"],
        sentiment=result["sentiment"],
        message=message,
    )


@router.get("/feedback/{history_id}", response_model=FeedbackResponse)
def get_feedback(
    history_id: str,
    username: str = Depends(get_current_user),
):
    """获取某条查询的反馈状态"""
    result = get_feedback_for_history(history_id, username)
    if result:
        return FeedbackResponse(
            success=True,
            id=result["id"],
            sentiment=result["sentiment"],
            message="",
        )
    return FeedbackResponse(
        success=False,
        id="",
        sentiment="",
        message="未反馈",
    )


@router.delete("/feedback/{history_id}", response_model=FeedbackResponse)
def remove_feedback(
    history_id: str,
    username: str = Depends(get_current_user),
):
    """取消/删除反馈"""
    deleted = delete_feedback(history_id, username)
    return FeedbackResponse(
        success=deleted,
        id="",
        sentiment="",
        message="反馈已取消" if deleted else "无反馈记录",
    )
