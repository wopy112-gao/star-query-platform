"""星宝语料场景查询系统 — SQL 质量门禁

包含两类校验：
1. 维度校验：用户问题要求的维度与 SQL GROUP BY 列是否匹配
2. 合理性校验：查询结果的数据是否合理（比率、聚合值等）
"""

import re
from typing import Optional

from sql_engine import engine
from intent_schemas import QueryIntent

# ============================================================
# 维度关键词映射表
# ============================================================

# 用户问题中的维度关键词 → 对应数据库列名
_DIMENSION_MAP: dict[tuple[str, ...], str] = {
    # 地域维度
    ("省份", "省分布", "按省份", "各省", "省级"): "省份",
    ("城市", "城市分布", "按城市", "各城市"): "城市",
    # 时间维度
    ("月", "月度", "月份", "每月", "按月"): "ydate",
    ("日", "日期", "每日", "按天"): "ydate",
    ("年", "年度", "每年"): "ydate",
    # 疾病维度
    ("疾病", "疾病分布", "各疾病", "病种"): "疾病名称",
    # 产品/药品维度
    ("药品", "产品", "品类"): "订单药品",
    # 门店维度
    ("门店", "药店", "店铺", "各门店"): "门店",
    # 连锁维度
    ("连锁", "品牌"): "连锁",
}


def _extract_expected_dimensions(question: str) -> list[str]:
    """从用户问题中提取期望的聚合维度列名"""
    q = question.strip().lower()
    expected: list[str] = []

    for keywords, col in _DIMENSION_MAP.items():
        for kw in keywords:
            if kw in q:
                expected.append(col)
                break  # 同一组关键词只匹配一次

    return expected


def _extract_group_by_columns(sql: str) -> list[str]:
    """从 SQL 中提取 GROUP BY 涉及的列名"""
    sql_upper = sql.upper()

    # 找到 GROUP BY 关键字的位置
    match = re.search(r"\bGROUP\s+BY\b", sql_upper)
    if not match:
        return []

    # 取 GROUP BY 之后到 ORDER BY / LIMIT / HAVING / UNION 之前的内容
    after_group = sql[match.end():]
    end_match = re.search(
        r"\b(ORDER\s+BY|LIMIT|HAVING|UNION|INTERSECT|EXCEPT)\b",
        after_group,
        flags=re.IGNORECASE,
    )
    if end_match:
        after_group = after_group[:end_match.start()]

    columns_text = after_group.strip().rstrip(";").strip()
    if not columns_text:
        return []

    # 提取列名（支持 列名, 列名 AS 别名, 函数(列名) 等）
    cols = []
    for col_part in columns_text.split(","):
        col_part = col_part.strip()
        # 去掉 AS 别名
        col_part = re.split(r"\s+AS\s+", col_part, flags=re.IGNORECASE)[0].strip()
        # 去掉函数调用，取里面的列名
        func_match = re.match(r"(\w+)\((.+)\)", col_part)
        if func_match:
            col_part = func_match.group(2).strip()
        # 去掉列名上的引号/反引号
        col_part = col_part.strip('"').strip("`").strip("'").strip("[]")
        if col_part and col_part not in cols:
            cols.append(col_part)

    return cols


def validate_dimensions(question: str, sql: str) -> list[str]:
    """维度校验

    检查用户问题中提到的维度（省份、城市、月份等）
    是否与 SQL 的 GROUP BY 列匹配。

    Args:
        question: 用户的自然语言问题
        sql: 生成的 SQL 语句

    Returns:
        警告信息列表（空列表 = 校验通过）
    """
    warnings: list[str] = []
    if not sql or sql.upper().startswith("--ERROR"):
        return warnings

    # 只有 SELECT 查询需要校验
    if not sql.upper().strip().startswith("SELECT"):
        return warnings

    expected_dims = _extract_expected_dimensions(question)
    if not expected_dims:
        return warnings  # 没有明确维度要求，跳过

    has_group_by = "GROUP BY" in sql.upper()

    # 如果有预期维度但没有 GROUP BY → 警告
    if expected_dims and not has_group_by:
        dim_names = "、".join([_col_to_display_name(d) for d in expected_dims])
        warnings.append(
            f'您提到了按「{dim_names}」分析，但当前查询没有分组聚合（GROUP BY），'
            f"如需按维度统计请重新提交"
        )
        return warnings

    if not has_group_by:
        return warnings

    # 提取 GROUP BY 中的列名
    group_cols = _extract_group_by_columns(sql)
    group_cols_lower = [c.lower() for c in group_cols]

    for dim_col in expected_dims:
        dim_lower = dim_col.lower()
        # 检查该维度的列是否出现在 GROUP BY 中
        # 允许列名出现在 GROUP BY 或出现在 SELECT 列表（没有 GROUP BY 时）
        if dim_lower not in group_cols_lower and dim_lower not in sql.lower():
            # 特殊处理：time functions like SUBSTR(ydate, 1, 7) AS 月份
            if dim_col == "ydate" and any("SUBSTR" in c.upper() or "DATE_" in c.upper() for c in group_cols):
                continue
            display_name = _col_to_display_name(dim_col)
            warnings.append(
                f'您要求按「{display_name}」维度分析，'
                f'但 GROUP BY 的列是「{", ".join(group_cols)}」'
            )

    return warnings


def _col_to_display_name(col: str) -> str:
    """列名转中文显示名"""
    mapping = {
        "省份": "省份",
        "城市": "城市",
        "ydate": "时间",
        "疾病名称": "疾病",
        "门店": "门店",
        "连锁": "连锁品牌",
        "订单药品": "药品",
    }
    return mapping.get(col, col)


# ============================================================
# 合理性校验
# ============================================================


def sanity_check_results(sql: str, rows: list[dict]) -> list[str]:
    """结果合理性校验

    在 SQL 执行后对结果数据进行合理性检查。

    Args:
        sql: 执行的 SQL 语句
        rows: 查询结果行

    Returns:
        警告信息列表（空列表 = 校验通过）
    """
    warnings: list[str] = []
    if not rows:
        return warnings

    sql_upper = sql.upper()
    row_count = len(rows)

    # ---- 检查1：百分比/比率值是否超过合理范围 ----
    for field in _find_numeric_columns(rows):
        values = [r.get(field) for r in rows if r.get(field) is not None]
        if not values:
            continue

        field_lower = field.lower()

        # 检查率类字段（成交率、占比等）是否超过 100%
        if any(kw in field_lower for kw in ["率", "占比", "比例", "百分比"]):
            over_100 = [v for v in values if isinstance(v, (int, float)) and v > 100.0]
            if over_100:
                warnings.append(
                    f'「{field}」列出现超过 100% 的值（最大 {max(over_100):.1f}%），'
                    f"请检查分母是否准确"
                )
            continue

        # 检查数值字段是否有负数（场景数、计数类不应为负）
        if any(kw in field_lower for kw in ["场景数", "计数", "数量", "人数", "行数"]):
            negatives = [v for v in values if isinstance(v, (int, float)) and v < 0]
            if negatives:
                warnings.append(
                    f'「{field}」列出现负数（最小 {min(negatives)}），'
                    f"计数类字段不应为负，请检查数据"
                )
            continue

    # ---- 检查2：成交场景数不能大于总场景数 ----
    scene_fields = [f for f in _find_numeric_columns(rows)
                    if any(kw in f.lower() for kw in ["场景数", "场景"])]
    deal_fields = [f for f in _find_numeric_columns(rows)
                   if any(kw in f.lower() for kw in ["成交", "达成"])]

    for scene_col in scene_fields:
        for deal_col in deal_fields:
            for row in rows:
                scene_val = row.get(scene_col)
                deal_val = row.get(deal_col)
                if (isinstance(scene_val, (int, float)) and
                        isinstance(deal_val, (int, float)) and
                        deal_val > scene_val):
                    warnings.append(
                        f'「{deal_col}」（{deal_val}）大于「{scene_col}」（{scene_val}），'
                        f"成交场景数不应超过总场景数，请检查统计口径"
                    )
                    break

    # ---- 检查3：聚合结果行数异常提示 ----
    if "GROUP BY" in sql_upper and row_count > 1:
        # 提取 GROUP BY 的列
        group_cols = _extract_group_by_columns(sql)
        group_cols_lower = [c.lower() for c in group_cols]

        # 按省份聚合不应超过 ~34 个
        if "省份" in group_cols_lower and row_count > 35:
            warnings.append(
                f"按省份聚合得到 {row_count} 行，"
                f"超过全国省份数量（约34个），可能 GROUP BY 了其他列"
            )
        # 按疾病聚合不应太多
        # 注意：如果 SQL 有 LIMIT N 且 row_count == N，说明结果被 LIMIT 截断，
        # 实际数据可能更多，不应告警（多适应症药品天然覆盖多种疾病场景）
        limit_clause = re.search(r"LIMIT\s+(\d+)", sql, re.IGNORECASE)
        has_limit = limit_clause is not None
        limit_val = int(limit_clause.group(1)) if limit_clause else 0
        if has_limit and limit_val > 0 and row_count >= limit_val:
            pass  # 被 LIMIT 截断，跳过行数告警
        elif "疾病名称" in group_cols_lower and row_count > 30:
            warnings.append(
                f"按疾病聚合得到 {row_count} 行，"
                f"结果较多，请确认是否为目标分析范围"
            )

    # ---- 检查4：用户明确问"成交"但没有交易是否达成条件 ----
    if any(kw in sql_upper for kw in ["成交", "达成"]):
        has_where = "WHERE" in sql_upper
        has_deal_condition = "交易是否达成" in sql_upper
        if has_where and not has_deal_condition:
            # 有成交关键词但没过滤交易是否达成
            all_deal_positive = all(
                r.get("交易是否达成") == "是"
                for r in rows[:20]
                if r.get("交易是否达成") is not None
            )
            if not all_deal_positive:
                warnings.append(
                    "您的问题涉及成交场景，但查询未按「交易是否达成='是'」过滤，"
                    "结果可能包含未成交场景"
                )

    # ---- 检查5：合计值警告（某些列的总和超过合理范围） ----
    # 跳过 LIMIT 查询（TOP N 场景下占比合计必然 < 100%，校验无意义）
    has_limit = bool(re.search(r"\bLIMIT\b", sql, re.IGNORECASE))
    if not has_limit:
        for field in _find_numeric_columns(rows):
            values = [r.get(field) for r in rows
                      if isinstance(r.get(field), (int, float))]
            if not values or len(values) < 2:
                continue

            total = sum(values)
            field_lower = field.lower()

            # 占比合计不应显著超过 100
            if any(kw in field_lower for kw in ["占比", "比例", "百分比"]):
                if total > 105:  # 允许 5% 的舍入误差
                    warnings.append(
                        f'「{field}」各列占比合计为 {total:.1f}%，超过 100%，'
                        f"请检查是否有重复统计"
                    )
                elif total < 95:
                    warnings.append(
                        f'「{field}」各列占比合计仅为 {total:.1f}%，未满 100%，'
                        f"可能有未覆盖的分类"
                    )

    return warnings


def _find_numeric_columns(rows: list[dict]) -> list[str]:
    """找出结果中的数值列"""
    if not rows:
        return []
    numeric_cols = []
    for col in rows[0].keys():
        for row in rows:
            val = row.get(col)
            if isinstance(val, (int, float)):
                numeric_cols.append(col)
                break
    return numeric_cols


# ============================================================
# 时间一致性校验（v4 新增）
# ============================================================

# 时间关键词检测模式
_TIME_DETECT_PATTERNS = [
    re.compile(r"最近\s*\d+\s*天"),      # 最近7天
    re.compile(r"最近\s*\d+\s*日"),      # 最近7日
    re.compile(r"近\s*\d+\s*天"),        # 近30天
    re.compile(r"近\s*\d+\s*日"),        # 近30日
    re.compile(r"近\s*\d+\s*个?月"),     # 近3个月
    re.compile(r"最近\s*\d+\s*个?月"),   # 最近3个月
    re.compile(r"近\s*\d+\s*年"),        # 近1年
    re.compile(r"最近\s*\d+\s*年"),      # 最近1年
    re.compile(r"本月|当月|这个月"),     # 本月
    re.compile(r"上个月|上月"),          # 上月
    re.compile(r"本周|这周"),            # 本周
    re.compile(r"今年|本年"),            # 今年
    re.compile(r"昨[天日]"),             # 昨天
    re.compile(r"今[天日]"),             # 今天
]


def validate_time_consistency(question: str, sql: str) -> list[str]:
    """验证时间一致性

    检查用户问题中是否提到时间范围，
    但生成的 SQL 中没有对应的 ydate 过滤。

    Args:
        question: 用户原始问题
        sql: 生成的 SQL 语句

    Returns:
        警告信息列表（空列表 = 校验通过）
    """
    warnings: list[str] = []
    if not sql or not question:
        return warnings

    # 检查问题中是否有时间关键词
    has_time_keyword = any(p.search(question) for p in _TIME_DETECT_PATTERNS)
    if not has_time_keyword:
        return warnings

    # 检查 SQL 中是否有 ydate 过滤
    sql_upper = sql.upper()
    has_ydate = "YDATE" in sql_upper
    has_date_filter = any(kw in sql_upper for kw in [
        "CURRENT_DATE", "INTERVAL", "DATE_TRUNC", "STRFTIME"
    ])

    if not has_ydate or not has_date_filter:
        # 提取时间短语
        time_phrase = ""
        for p in _TIME_DETECT_PATTERNS:
            m = p.search(question)
            if m:
                time_phrase = m.group(0)
                break
        if not time_phrase:
            time_phrase = question

        warnings.append(
            f'您的问题提到时间范围「{time_phrase}」，'
            f'但 SQL 中缺少 ydate 时间过滤条件。'
        )

    return warnings


# ============================================================
# 结构化意图一致性校验（新增，v3）
# ============================================================


def validate_intent_consistency(intent: QueryIntent, sql: str) -> list[str]:
    """基于结构化意图校验 SQL 一致性

    比 validate_dimensions(question, sql) 更精确，因为使用了
    意图中的结构化信息而非关键词提取。

    检查项：
    1. 维度校验：intent.dimension 与 SQL GROUP BY 是否一致
    2. 成交口径校验：intent 涉及成交 → SQL 必须含 交易是否达成='是'
    3. 条件实体校验：intent 的 conditions 与 SQL WHERE 是否一致
    4. 查询模式校验：detail vs aggregation 的一致性

    Args:
        intent: 结构化查询意图
        sql: 生成的 SQL 语句

    Returns:
        警告/错误信息列表（空列表 = 全部通过）
    """
    warnings: list[str] = []
    if not sql or sql.strip().upper().startswith("--ERROR"):
        return warnings

    sql_upper = sql.upper()

    # ---- 检查1：维度校验 ----
    if intent.dimension:
        group_cols = _extract_group_by_columns(sql)
        # 维度映射（意图维度名 → 数据库列名）
        dim_to_sql = {
            "省份": ["省份"],
            "城市": ["城市"],
            "疾病名称": ["疾病名称"],
            "门店": ["门店"],
            "连锁": ["连锁"],
            "月度": ["月份"],    # SUBSTR(ydate,1,7) AS 月份
            "顾客性别": ["顾客性别"],
            "顾客年龄": ["顾客年龄"],
            "场景时长档": ["时长档位"],
            "场景提及药品": ["t.drug"],    # JSON 数组 UNNEST 展开后的别名
            "订单药品": ["t.drug"],
            "顾客点名药品": ["t.drug"],
        }
        expected_sql_cols = dim_to_sql.get(intent.dimension, [intent.dimension])
        has_dimension = any(
            col_name in group_cols
            for col_name in expected_sql_cols
        )
        if not has_dimension and group_cols:
            # 如果 SQL 有 GROUP BY，但不是意图指定的维度
            warnings.append(
                f"维度不匹配：意图要求按「{intent.dimension}」分组，"
                f"但 SQL 按 {group_cols} 分组"
            )
        elif has_dimension:
            pass  # 维度匹配
        elif not group_cols:
            # SQL 没 GROUP BY 但意图需要分布 → 已经是 distribution pattern，
            # 可能 SQL 生成有问题，但让路由兜底处理
            # 注意：如果 intent.query_pattern 不是 distribution/top_n/ranking/trend，
            # 而是 single_stat（如"覆盖多少个城市"），不该告警
            if intent.query_pattern in ("distribution", "top_n", "ranking", "trend"):
                warnings.append(
                    f"意图为分布模式（维度：{intent.dimension}），"
                    f"但 SQL 中没有 GROUP BY 子句"
                )

    # ---- 检查2：成交口径校验 ----
    deal_keywords_in_agg = ["成交", "达成"]
    is_deal_related = any(kw in intent.agg for kw in deal_keywords_in_agg)
    if is_deal_related:
        # 区分"未成交" vs "成交"
        if "未成交" in intent.agg:
            expected_condition = "交易是否达成"
            expected_value = "'否'"
        else:
            expected_condition = "交易是否达成"
            expected_value = "'是'"
        # 用灵活匹配处理 SQL 中可能的空格差异（交易是否达成 = '是' vs 交易是否达成='是'）
        import re
        if not re.search(rf"交易是否达成\s*=\s*{expected_value}", sql):
            warnings.append(
                f"成交口径缺失：聚合方式为「{intent.agg}」，"
                f"但 SQL 中没有 交易是否达成 = {expected_value} 条件"
            )

    # ---- 检查3：条件实体校验 ----
    for cond in intent.conditions:
        ctype, cval = cond.type, cond.value
        # 疾病条件 → SQL 应包含对应的 LIKE 或 IN
        if ctype == "disease":
            like_pattern = f"LIKE '%{cval}%'"
            in_pattern = f"'{cval}'"  # IN ('糖尿病', '咳嗽') 等
            if like_pattern not in sql and in_pattern not in sql:
                warnings.append(
                    f"条件缺失：意图包含疾病「{cval}」的筛选，"
                    f"但 SQL 中没有 {like_pattern}"
                )
        # 药品条件 → SQL 应包含药品名
        elif ctype in ("drug_any", "drug_named", "drug_mentioned", "drug_ordered"):
            if cval not in sql:
                warnings.append(
                    f"条件缺失：意图包含药品「{cval}」的筛选，"
                    f"但 SQL 中没有该药品名"
                )

    # ---- 检查4：查询模式校验 ----
    # distribution / top_n → 应有 GROUP BY
    if intent.query_pattern in ("distribution", "top_n", "ranking", "trend"):
        if "GROUP BY" not in sql_upper:
            warnings.append(
                f"查询模式为「{intent.query_pattern}」（需要分组聚合），"
                f"但 SQL 中没有 GROUP BY"
            )
    # detail → 不应有 GROUP BY
    if intent.query_pattern == "detail" and "GROUP BY" in sql_upper:
        warnings.append(
            "查询模式为「detail」（逐条数据），"
            "但 SQL 中有 GROUP BY 聚合，请确认查询意图"
        )

    return warnings


def _extract_group_by_columns(sql: str) -> list[str]:
    """从 SQL 中提取 GROUP BY 涉及的列名（与 validate_dimensions 共享）"""
    sql_upper = sql.upper()

    match = re.search(r"\bGROUP\s+BY\b", sql_upper)
    if not match:
        return []

    after_group = sql[match.end():]
    end_match = re.search(
        r"\b(ORDER\s+BY|LIMIT|HAVING|UNION|INTERSECT|EXCEPT)\b",
        after_group,
        flags=re.IGNORECASE,
    )
    if end_match:
        after_group = after_group[:end_match.start()]

    columns_text = after_group.strip().rstrip(";").strip()
    if not columns_text:
        return []

    cols = []
    for col_part in columns_text.split(","):
        col_part = col_part.strip()
        col_part = re.split(r"\s+AS\s+", col_part, flags=re.IGNORECASE)[0].strip()
        func_match = re.match(r"(\w+)\((.+)\)", col_part)
        if func_match:
            col_part = func_match.group(2).strip()
        col_part = col_part.strip('"').strip("`").strip("'").strip("[]")
        if col_part and col_part not in cols:
            cols.append(col_part)

    return cols
