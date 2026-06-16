"""星宝语料场景查询系统 — SQL 渲染引擎

负责将模板 + 结构化意图参数渲染为最终 SQL。
处理条件组合、维度字段映射、聚合公式替换。
"""

from __future__ import annotations

from typing import Optional

from intent_schemas import QueryIntent, Condition

# ============================================================
# 条件类型 → SQL WHERE 子句 映射表
# ============================================================

CONDITION_SQL_MAP: dict[str, str] = {
    # 疾病
    "disease": "疾病名称 LIKE '%{value}%'",
    # 药品（三个字段任一匹配）
    "drug_any": (
        "(顾客点名药品 LIKE '%{value}%' "
        "OR 场景提及药品 LIKE '%{value}%' "
        "OR 订单药品 LIKE '%{value}%')"
    ),
    # 顾客点名
    "drug_named": "顾客点名药品 LIKE '%{value}%'",
    # 场景提及
    "drug_mentioned": "场景提及药品 LIKE '%{value}%'",
    # 订单药品
    "drug_ordered": "订单药品 LIKE '%{value}%'",
    # 成交
    "deal_yes": "交易是否达成='是'",
    # 未成交
    "deal_no": "交易是否达成='否'",
    # 点名未成交
    "named_not_ordered": (
        "(顾客点名药品 LIKE '%{value}%' "
        "AND (交易是否达成='否' "
        "OR 订单药品 NOT LIKE '%{value}%'))"
    ),
    # 提及未点名
    "mentioned_not_named": (
        "(场景提及药品 LIKE '%{value}%' "
        "AND 顾客点名药品 NOT LIKE '%{value}%')"
    ),
    # 提及未点名又成交
    "mn_no_named_ordered": (
        "(场景提及药品 LIKE '%{value}%' "
        "AND 顾客点名药品 NOT LIKE '%{value}%' "
        "AND 订单药品 LIKE '%{value}%')"
    ),
    # 联合用药
    "combination": "是否联合用药='是'",
    # 顾客性别
    "gender": "顾客性别='{value}'",
    # 活动推荐
    "active_recommend": "是否场景下活动推荐='是'",
    # 活动参与
    "active_participate": "活动是否参与='是'",
    # 门店类型
    "store_type": "连锁 LIKE '%{value}%'",
    # 信任度
    "trust": "顾客信任度='{value}'",
    # 时间范围（特殊处理：由 _render_time_range_condition 动态生成）
    "time_range": "__DYNAMIC__",
    # ↓↓↓ 2026-06-11 新增条件类型 ↓↓↓
    # 店员提及药品（主动提及）
    "clerk_mention": "店员提及药品JSON LIKE '%{value}%'",
    # 店员推荐药品（主动推荐）
    "clerk_recommend": "店员推荐药品JSON LIKE '%{value}%'",
    # 店员推荐药品（别名，同 clerk_recommend）
    "drug_recommend": "店员推荐药品JSON LIKE '%{value}%'",
    # 年龄分层
    "age_layer": "用药人年龄分层='{value}'",
    # 联合用药动作（店员实际行为，区别于AI判断的'是否联合用药'）
    "combo_action": "联合用药动作='1'",
    # 商用数据过滤
    "commercial": "是否商用='1'",
    # 业务置信度过滤
    "confidence": "业务置信度='{value}'",
}

# ============================================================
# 维度 → SQL 列名 映射表
# ============================================================

DIMENSION_SQL_MAP: dict[str, str] = {
    "省份": "省份",
    "城市": "城市",
    "疾病名称": "疾病名称",
    "门店": "门店",
    "连锁": "连锁",
    "月度": "月份",
    "顾客性别": "顾客性别",
    "顾客年龄": "顾客年龄",
    "顾客信任度": "顾客信任度",
    "场景时长档": "时长档位",
    "门店类型": "门店类型",
    # ↓↓↓ 2026-06-11 新增维度 ↓↓↓
    "用药人年龄分层": "用药人年龄分层",
    "联合用药动作": "联合用药动作",
    "是否商用": "是否商用",
    "综合置信度评分": "综合置信度评分",
    "业务置信度": "业务置信度",
    "场景完整度": "场景完整度",
    "切割置信度分值": "切割置信度分值",
    "切割完整度分值": "切割完整度分值",
    "活动时间占比": "活动时间占比",
    "店员提及药品JSON": "店员提及药品JSON",
    "店员推荐药品JSON": "店员推荐药品JSON",
}

# 按维度的特殊 SQL 处理
DIMENSION_SQL_TEMPLATES: dict[str, str] = {
    "月度": "strftime(ydate, '%Y-%m') AS 月份",
    "场景时长档": (
        "CASE "
        "WHEN 场景时长 < 60 THEN '1分钟以内' "
        "WHEN 场景时长 < 120 THEN '1-2分钟' "
        "WHEN 场景时长 < 180 THEN '2-3分钟' "
        "WHEN 场景时长 < 300 THEN '3-5分钟' "
        "WHEN 场景时长 < 600 THEN '5-10分钟' "
        "ELSE '10分钟以上' END AS 时长档位"
    ),
}

# JSON 数组维度（药品字段需要 UNNEST 展开）
DRUG_DIMENSIONS = {"场景提及药品", "订单药品", "顾客点名药品", "店员提及药品JSON", "店员推荐药品JSON"}

# 药品维度 → (SELECT列名, UNNEST JOIN子句, 字段别名)
# ⚠️ UNNEST 的别名必须与 _render_dimension() 返回的 t.drug 一致
DRUG_UNNEST_MAP: dict[str, tuple[str, str, str]] = {
    "场景提及药品": (
        "TRIM(t.drug, ' \"') AS 场景提及药品",
        "LATERAL UNNEST(string_split(TRIM(场景提及药品, '[]'), ',')) AS t(drug)",
        "t.drug",
    ),
    "订单药品": (
        "TRIM(t.drug, ' \"') AS 订单药品",
        "LATERAL UNNEST(string_split(TRIM(订单药品, '[]'), ',')) AS t(drug)",
        "t.drug",
    ),
    "顾客点名药品": (
        "TRIM(t.drug, ' \"') AS 顾客点名药品",
        "LATERAL UNNEST(string_split(TRIM(顾客点名药品, '[]'), ',')) AS t(drug)",
        "t.drug",
    ),
    # ↓↓↓ 2026-06-11 新增 JSON 维度 ↓↓↓
    "店员提及药品JSON": (
        "TRIM(t.drug, ' \"') AS 店员提及药品JSON",
        "LATERAL UNNEST(string_split(TRIM(店员提及药品JSON, '[]'), ',')) AS t(drug)",
        "t.drug",
    ),
    "店员推荐药品JSON": (
        "TRIM(t.drug, ' \"') AS 店员推荐药品JSON",
        "LATERAL UNNEST(string_split(TRIM(店员推荐药品JSON, '[]'), ',')) AS t(drug)",
        "t.drug",
    ),
}


class SQLRenderer:
    """SQL 渲染引擎"""

    def render(self, template_obj: dict, intent: QueryIntent) -> str:
        """
        渲染最终 SQL。

        流程：
        1. 加载模板的 SQL 骨架
        2. 生成 WHERE 子句（根据 intent.conditions）
        3. 替换 {dimension} 为意图维度
        4. 替换 {conditions} / {conditions_extra} 为条件子句
        5. 替换 {limit} 为意图行数限制
        6. 如果是药品维度（JSON 数组），执行 UNNEST 展开
        7. 清理多余的 WHERE / AND
        """
        sql = template_obj["sql_template"]

        # Step 1: 渲染 {conditions} → 完整 WHERE 子句（含 WHERE 前缀）
        conditions_sql = self._render_conditions(intent.conditions, intent)

        # 方案B：未知条件类型 → 触发 LLM fallback
        if conditions_sql == "__LLM_FALLBACK__":
            print(f"[SQLRender] 条件含未知类型，触发 LLM fallback")
            return "__LLM_FALLBACK__"

        sql = sql.replace("{conditions}", conditions_sql)

        # Step 2: 渲染 {conditions_extra} → AND 连接的条件（不含 WHERE 前缀）
        conditions_extra = self._render_conditions_extra(intent.conditions)
        if conditions_extra == "__LLM_FALLBACK__":
            print(f"[SQLRender] 条件含未知类型（extra），触发 LLM fallback")
            return "__LLM_FALLBACK__"

        sql = sql.replace("{conditions_extra}", conditions_extra)

        # Step 2b: 模板有 {dimension} 但维度为空 → LLM fallback
        if '{dimension}' in sql and (not intent.dimension or intent.dimension.strip() == ''):
            print(f"[SQLRender] 模板需要 dimension 但意图未提供，触发 LLM fallback")
            return "__LLM_FALLBACK__"

        # Step 3: 渲染 {dimension} → 维度 SQL
        dim_col = self._render_dimension(intent.dimension)
        sql = sql.replace("{dimension}", dim_col)

        # Step 3b: 如果是药品维度（JSON 数组字段），执行 UNNEST 展开
        dim_value = intent.dimension
        if dim_value and dim_value in DRUG_DIMENSIONS:
            sql = self._apply_drug_unnest(sql, dim_value)

        # Step 4: 渲染 {limit} → 行数限制
        sql = sql.replace("{limit}", str(intent.limit))

        # Step 5: 清理多余的 AND / WHERE
        sql = self._clean_sql(sql)

        # Step 6: 处理去重计数（dedup_field）— 替换默认的 COUNT(DISTINCT 场景ID)
        if intent.dedup_field:
            old_count = "COUNT(DISTINCT 场景ID)"
            new_count = f"COUNT(DISTINCT {intent.dedup_field})"
            if old_count in sql:
                sql = sql.replace(old_count, new_count)
                print(f"[SQLRender] 去重计数替换: {old_count} → {new_count}")
            # 如果模板中的别名包含「场景数」，一并替换
            field_label = intent.dedup_field.replace("ID", "").replace("ID", "")
            alias_map = {"AS 场景数": f"AS {field_label}数"}
            for old_alias, new_alias in alias_map.items():
                if old_alias in sql:
                    sql = sql.replace(old_alias, new_alias)

        return sql

    def _render_conditions(self, conditions: list[Condition], intent: Optional[QueryIntent] = None) -> str:
        """将条件列表渲染为 WHERE 子句（含 WHERE 前缀）

        v2: 同类型多条件默认用 OR 连接（解决对比查询如"糖尿病 vs 咳嗽"的 AND 冲突）
        """
        if not conditions:
            return ""

        where_parts = []
        needs_fallback = False
        for cond in conditions:
            fragment = self._render_single_condition(cond)
            if fragment == "__LLM_FALLBACK__":
                needs_fallback = True
                continue
            if fragment:
                where_parts.append(fragment)

        if needs_fallback:
            return "__LLM_FALLBACK__"

        if not where_parts:
            return ""

        # 检测同类型分组：同类型条件之间用 OR，不同类型之间用 AND
        grouped: dict[str, list[str]] = {}
        for i, cond in enumerate(conditions):
            ct = cond.type
            if ct not in grouped:
                grouped[ct] = []
            if i < len(where_parts):
                grouped[ct].append(where_parts[i])

        final_parts = []
        for ct, fragments in grouped.items():
            if len(fragments) > 1:
                # 同类型多条件 → OR 连接（如：疾病 LIKE '%糖尿病%' OR 疾病 LIKE '%咳嗽%'）
                final_parts.append(f"({' OR '.join(fragments)})")
            else:
                final_parts.append(fragments[0])

        combined = " AND ".join(final_parts)
        return f"WHERE {combined}"

    def _render_conditions_extra(self, conditions: list[Condition]) -> str:
        """将条件列表渲染为 AND 连接的条件片段（不含 WHERE 前缀）

        用于 sql_template 中已有固定 WHERE 条件时的额外条件追加。
        如: "FROM data WHERE 交易是否达成='是' {conditions_extra}"
        """
        if not conditions:
            return ""

        where_parts = []
        needs_fallback = False
        for cond in conditions:
            fragment = self._render_single_condition(cond)
            if fragment == "__LLM_FALLBACK__":
                needs_fallback = True
                continue
            if fragment:
                where_parts.append(fragment)

        if needs_fallback:
            return "__LLM_FALLBACK__"

        if not where_parts:
            return ""

        combined = " AND ".join(where_parts)
        return f"AND {combined}"

    def _render_single_condition(self, cond: Condition) -> str:
        """渲染单个条件为 SQL 片段（处理 geo、time_range 等特殊类型）"""
        # 地域条件：根据值自动判断省份还是城市
        if cond.type == "geo":
            return self._render_geo_condition(cond.value)

        # 时间范围条件：动态生成日期过滤
        if cond.type == "time_range":
            return self._render_time_range_condition(cond.value)

        sql_fragment = CONDITION_SQL_MAP.get(cond.type)
        if not sql_fragment or sql_fragment == "__DYNAMIC__":
            # 方案B：未知条件类型 → 标记需要 LLM fallback
            print(f"[SQLRender] 未知条件类型「{cond.type}」，标记 LLM fallback")
            return "__LLM_FALLBACK__"
        return sql_fragment.replace("{value}", cond.value)

    def _render_geo_condition(self, value: str) -> str:
        """将地域条件值渲染为 SQL

        策略：
        - 含"省"字（且不以"市"结尾）→ 省份精确匹配
        - 含"市"字 → 城市模糊匹配
        - 纯名称 → 同时查省份和城市
        """
        if value.endswith("省"):
            return f"省份='{value}'"
        if value.endswith("市"):
            return f"城市 LIKE '%{value}%'"
        # 可能是简称 → 同时查省份和城市
        return f"(省份 LIKE '%{value}%' OR 城市 LIKE '%{value}%')"

    def _render_time_range_condition(self, value: str) -> str:
        """将标准化时间范围值渲染为 DuckDB SQL WHERE 子句

        输入值由 query_intent._parse_time_range_text 统一标准化，
        格式如 "最近7天" "最近30天" "本月" "今年" "昨天" 等。

        返回完整的 SQL 条件字符串（不含 WHERE 前缀），如:
            ydate >= CURRENT_DATE - INTERVAL '7' DAY
        """
        import re

        v = value.strip()

        # 最近N天
        m = re.match(r"^最近\s*(\d+)\s*天$", v)
        if m:
            n = m.group(1)
            return f"ydate >= CURRENT_DATE - INTERVAL '{n}' DAY"

        # 最近N周
        m = re.match(r"^最近\s*(\d+)\s*周$", v)
        if m:
            n = int(m.group(1)) * 7
            return f"ydate >= CURRENT_DATE - INTERVAL '{n}' DAY"

        # 最近N个月
        m = re.match(r"^最近\s*(\d+)\s*个月$", v)
        if m:
            n = m.group(1)
            return f"ydate >= CURRENT_DATE - INTERVAL '{n}' MONTH"

        # 最近N年
        m = re.match(r"^最近\s*(\d+)\s*年$", v)
        if m:
            n = m.group(1)
            return (
                f"strftime(ydate, '%Y') >= "
                f"CAST(strftime(CURRENT_DATE, '%Y') AS INTEGER) - {n}"
            )

        # 本月
        if v == "本月":
            return f"strftime(ydate, '%Y-%m') = strftime(CURRENT_DATE, '%Y-%m')"

        # 上月
        if v == "上月":
            return (
                f"strftime(ydate, '%Y-%m') = "
                f"strftime(CURRENT_DATE - INTERVAL '1' MONTH, '%Y-%m')"
            )

        # 本周
        if v == "本周":
            return (
                f"ydate >= DATE_TRUNC('week', CURRENT_DATE) "
                f"AND ydate < DATE_TRUNC('week', CURRENT_DATE) + INTERVAL '7' DAY"
            )

        # 具体年份：2026年、2025年等
        m = re.match(r"^(\d{4})\s*年$", v)
        if m:
            year = m.group(1)
            return f"strftime(ydate, '%Y') = '{year}'"

        # 2026年3月以前/之前 → ydate < '2026-03-01'
        m = re.match(r"^(\d{4})\s*年\s*(\d{1,2})\s*个?月?份?\s*(以前|之前)$", v)
        if m:
            year = m.group(1)
            month = m.group(2).zfill(2)
            return f"ydate < '{year}-{month}-01'"

        # 2026年1月份以后/之后/后 → ydate >= '2026-02-01'
        m = re.match(r"^(\d{4})\s*年\s*(\d{1,2})\s*个?月?份?\s*(以后|之后|后)$", v)
        if m:
            year = m.group(1)
            month = str(int(m.group(2)) + 1).zfill(2)
            if int(m.group(2)) >= 12:
                year = str(int(year) + 1)
                month = "01"
            return f"ydate >= '{year}-{month}-01'"

        # 2026年3月 → ydate between '2026-03-01' and '2026-03-31'
        m = re.match(r"^(\d{4})\s*年\s*(\d{1,2})\s*个?月?$", v)
        if m:
            year = m.group(1)
            month = m.group(2).zfill(2)
            return f"(strftime(ydate, '%Y-%m') = '{year}-{month}')"

        # 今年
        if v == "今年":
            return f"strftime(ydate, '%Y') = strftime(CURRENT_DATE, '%Y')"

        # 昨天
        if v == "昨天":
            return f"ydate = CURRENT_DATE - INTERVAL '1' DAY"

        # 今天
        if v == "今天":
            return f"ydate = CURRENT_DATE"

        # 未知格式，fallback
        print(f"[SQLRender] 无法解析的时间范围: {v}")
        return ""

    def _render_dimension(self, dimension: Optional[str]) -> str:
        """将维度名渲染为 SQL 列名"""
        if not dimension:
            return ""

        # 药品维度（JSON 数组）→ 用 t.drug 别名表示，后面再展开
        if dimension in DRUG_DIMENSIONS:
            return f"t.drug AS {dimension}"

        # 检查是否有特殊 SQL 模板（如月度、时长档）
        if dimension in DIMENSION_SQL_TEMPLATES:
            return DIMENSION_SQL_TEMPLATES[dimension]

        # 普通维度映射
        return DIMENSION_SQL_MAP.get(dimension, dimension)

    def _apply_drug_unnest(self, sql: str, dimension: str) -> str:
        """将药品维度的 GROUP BY 改为 JSON 数组 UNNEST 展开

        药品字段（场景提及药品/订单药品/顾客点名药品）是 JSON 数组格式，
        不能直接 GROUP BY。需要将 JSON 数组展开为多行后再分组。

        输入模板 SQL:
            SELECT t.drug AS 场景提及药品, COUNT(DISTINCT 场景ID) AS 场景数
            FROM data ...

        输出:
            SELECT t.drug AS 场景提及药品, COUNT(DISTINCT data.场景ID) AS 场景数
            FROM data, LATERAL UNNEST(CAST(JSON(场景提及药品) AS VARCHAR[])) AS t(drug) ...
        """
        unnest_info = DRUG_UNNEST_MAP.get(dimension)
        if not unnest_info:
            return sql

        _, unnest_join, alias = unnest_info

        # 替换 FROM data → FROM data, LATERAL UNNEST(...) AS t(drug)
        sql = sql.replace("FROM data", f"FROM data, {unnest_join}", 1)

        # 替换 GROUP BY t.drug AS 维度 → GROUP BY t.drug
        sql = sql.replace(f"GROUP BY {alias} AS {dimension}", f"GROUP BY {alias}")

        # 替换 ORDER BY t.drug AS 维度 → ORDER BY t.drug
        sql = sql.replace(f"ORDER BY {alias} AS {dimension}", f"ORDER BY {alias}")

        # 给 COUNT(DISTINCT 场景ID) 加表名前缀避免歧义
        sql = sql.replace("COUNT(DISTINCT 场景ID)", "COUNT(DISTINCT data.场景ID)")

        return sql

    def _clean_sql(self, sql: str) -> str:
        """清理多余的 AND / WHERE"""
        import re

        # 清理 WHERE 后面紧跟 AND（conditions 为空时可能触发）
        sql = re.sub(r"WHERE\s+AND\s+", "WHERE ", sql, flags=re.IGNORECASE)

        # 清理孤立的 WHERE（后面没有条件）
        sql = re.sub(r"WHERE\s*\Z", "", sql)

        # 清理多余空格
        sql = re.sub(r"\s{2,}", " ", sql).strip()

        return sql


# 全局实例
renderer = SQLRenderer()
