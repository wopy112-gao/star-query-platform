"""星宝语料场景查询系统 — 预置查询模板匹配器（L1 v3）

改造内容：
1. 新增结构化模板元数据（pattern, agg, condition_types, dimension）
2. 新增 match_by_intent() 方法，支持四元组匹配
3. 新增 get_similar_templates() 方法，供 LLM 兜底时取参考示例
4. 保留 match(question) 原有的字符串匹配方式（向后兼容）
"""

from __future__ import annotations

import re
from typing import Optional

from intent_schemas import QueryIntent

# ============================================================
# 结构化模板清单
# 每个模板由两组匹配条件组成：
#   1. question_pattern（旧）：正则匹配用户问题字符串
#   2. intent_key（新）：(pattern, agg, condition_types, dimension) 四元组
# ============================================================

TEMPLATES = [
    # ===== single_stat 单值统计 =====
    {
        "id": "t1",
        "label": "总场景数",
        "question": "总场景数",
        "description": "查询数据集中的总独立场景数",
        "question_pattern": r"^总场景数$",
        "intent_key": {"pattern": "single_stat", "agg": "场景数", "condition_types": [], "dimension": None},
        "sql_template": "SELECT COUNT(DISTINCT 场景ID) AS 总场景数 FROM data {conditions}",
        "chart_type": "table_only",
    },
    {
        "id": "t5",
        "label": "问症率",
        "question": "问症率",
        "description": "统计总体问症率",
        "question_pattern": r"^(问症|问症率|询问症状)\s*(率|统计|分析)?$",
        "intent_key": {"pattern": "single_stat", "agg": "问症率", "condition_types": [], "dimension": None},
        "sql_template": (
            "SELECT "
            "COUNT(DISTINCT CASE WHEN 是否问症='是' THEN 场景ID END) AS 问症场景数, "
            "COUNT(DISTINCT 场景ID) AS 总场景数, "
            "ROUND(COUNT(DISTINCT CASE WHEN 是否问症='是' THEN 场景ID END) * 100.0 "
            "/ COUNT(DISTINCT 场景ID), 1) AS 问症率 "
            "FROM data {conditions}"
        ),
        "chart_type": "table_only",
    },
    {
        "id": "t6",
        "label": "联合用药率",
        "question": "联合用药率",
        "description": "统计总体联合用药率",
        "question_pattern": r"^(联合用药|联合用药率)\s*(率|统计|分析)?$",
        "intent_key": {"pattern": "single_stat", "agg": "联合用药率", "condition_types": [], "dimension": None},
        "sql_template": (
            "SELECT "
            "COUNT(DISTINCT CASE WHEN 是否联合用药='是' THEN 场景ID END) AS 联合用药场景数, "
            "COUNT(DISTINCT 场景ID) AS 总场景数, "
            "ROUND(COUNT(DISTINCT CASE WHEN 是否联合用药='是' THEN 场景ID END) * 100.0 "
            "/ COUNT(DISTINCT 场景ID), 1) AS 联合用药率 "
            "FROM data {conditions}"
        ),
        "chart_type": "table_only",
    },
    {
        "id": "t10",
        "label": "成交率",
        "question": "成交率",
        "description": "统计总体成交率",
        "question_pattern": r"^(成交|成交率|交易)\s*(率|统计|分析)?$",
        "intent_key": {"pattern": "single_stat", "agg": "成交率", "condition_types": [], "dimension": None},
        "sql_template": (
            "SELECT "
            "COUNT(DISTINCT CASE WHEN 交易是否达成='是' THEN 场景ID END) AS 成交场景数, "
            "COUNT(DISTINCT 场景ID) AS 总场景数, "
            "ROUND(COUNT(DISTINCT CASE WHEN 交易是否达成='是' THEN 场景ID END) * 100.0 "
            "/ COUNT(DISTINCT 场景ID), 1) AS 成交率 "
            "FROM data {conditions}"
        ),
        "chart_type": "table_only",
    },
    {
        "id": "t11",
        "label": "关键信息到达率",
        "question": "关键信息到达率",
        "description": "统计药师关键信息传递率",
        "question_pattern": r"^(关键信息|信息到达|关键信息到达率)\s*(率|统计|分析)?$",
        "intent_key": {"pattern": "single_stat", "agg": "关键信息到达率", "condition_types": [], "dimension": None},
        "sql_template": (
            "SELECT "
            "COUNT(DISTINCT CASE WHEN 是否关键信息到达='是' THEN 场景ID END) AS 关键信息到达场景数, "
            "COUNT(DISTINCT 场景ID) AS 总场景数, "
            "ROUND(COUNT(DISTINCT CASE WHEN 是否关键信息到达='是' THEN 场景ID END) * 100.0 "
            "/ COUNT(DISTINCT 场景ID), 1) AS 关键信息到达率 "
            "FROM data {conditions}"
        ),
        "chart_type": "table_only",
    },
    # 通用单值统计（带条件的 single_stat 由渲染器处理）
    {
        "id": "ts02",
        "label": "成交场景数",
        "question": "成交场景数",
        "description": "统计成交场景数",
        "question_pattern": r"^成交场景数$",
        "intent_key": {"pattern": "single_stat", "agg": "成交场景数", "condition_types": [], "dimension": None},
        "sql_template": "SELECT COUNT(DISTINCT 场景ID) AS 成交场景数 FROM data WHERE 交易是否达成='是'",
        "chart_type": "table_only",
    },
    {
        "id": "ts07",
        "label": "平均场景时长",
        "question": "平均场景时长",
        "description": "统计平均场景时长",
        "question_pattern": r"^(平均场景时长|场景平均时长|场景时长平均值)\s*(统计|分析)?$",
        "intent_key": {"pattern": "single_stat", "agg": "平均场景时长", "condition_types": [], "dimension": None},
        "sql_template": "SELECT ROUND(AVG(场景时长), 0) AS 平均场景时长 FROM data {conditions}",
        "chart_type": "table_only",
    },
    # ===== 2026-06-11 新增模板（新字段） =====
    {
        "id": "ts08",
        "label": "联合用药动作率",
        "question": "联合用药动作率",
        "description": "统计店员实际联合推荐比例（区别于AI判断的联合用药率）",
        "question_pattern": r"^(联合用药动作|实际联合|联合推荐)\s*(率|统计|分析)?$",
        "intent_key": {"pattern": "single_stat", "agg": "联合用药率", "condition_types": [], "dimension": None},
        "sql_template": (
            "SELECT "
            "COUNT(DISTINCT CASE WHEN 联合用药动作='1' THEN 场景ID END) AS 联合推荐场景数, "
            "COUNT(DISTINCT 场景ID) AS 总场景数, "
            "ROUND(COUNT(DISTINCT CASE WHEN 联合用药动作='1' THEN 场景ID END) * 100.0 "
            "/ NULLIF(COUNT(DISTINCT 场景ID), 0), 1) AS 联合推荐率 "
            "FROM data {conditions}"
        ),
        "chart_type": "table_only",
    },
    {
        "id": "ts09",
        "label": "商用场景率",
        "question": "商用场景率",
        "description": "统计数据达到商用标准的比例",
        "question_pattern": r"^(商用|数据质量|优质数据)\s*(率|统计|分析|占比)?$",
        "intent_key": {"pattern": "single_stat", "agg": "场景占比", "condition_types": [], "dimension": None},
        "sql_template": (
            "SELECT "
            "COUNT(DISTINCT CASE WHEN 是否商用='1' THEN 场景ID END) AS 商用场景数, "
            "COUNT(DISTINCT 场景ID) AS 总场景数, "
            "ROUND(COUNT(DISTINCT CASE WHEN 是否商用='1' THEN 场景ID END) * 100.0 "
            "/ NULLIF(COUNT(DISTINCT 场景ID), 0), 1) AS 商用率 "
            "FROM data {conditions}"
        ),
        "chart_type": "table_only",
    },
    {
        "id": "ts10",
        "label": "年龄分层分布",
        "question": "年龄分层分布",
        "description": "按用药人年龄分层统计场景数分布",
        "question_pattern": r"^(年龄|年龄段|年龄分层|用药年龄)\s*(分布|统计|排名|TOP\d+)?$",
        "intent_key": {"pattern": "distribution", "agg": "场景数", "condition_types": [], "dimension": "用药人年龄分层"},
        "sql_template": (
            "SELECT 用药人年龄分层, COUNT(DISTINCT 场景ID) AS 场景数, "
            "ROUND(COUNT(DISTINCT 场景ID) * 100.0 / (SELECT COUNT(DISTINCT 场景ID) FROM data), 1) AS 占比 "
            "FROM data {conditions} "
            "GROUP BY 用药人年龄分层 ORDER BY 场景数 DESC"
        ),
        "chart_type": "bar",
    },
    # ===== distribution 分布 =====
    {
        "id": "t2",
        "label": "疾病TOP10",
        "question": "疾病TOP10",
        "description": "按疾病名称统计场景数TOP10",
        "question_pattern": r"^(疾病|疾病名称|病种)\s*(TOP\d+|排名|分布)$",
        "intent_key": {"pattern": "top_n", "agg": "场景数", "condition_types": [], "dimension": "疾病名称"},
        "sql_template": (
            "SELECT 疾病名称, COUNT(DISTINCT 场景ID) AS 场景数, "
            "ROUND(COUNT(DISTINCT 场景ID) * 100.0 / (SELECT COUNT(DISTINCT 场景ID) FROM data), 1) AS 占比 "
            "FROM data GROUP BY 疾病名称 ORDER BY 场景数 DESC LIMIT {limit}"
        ),
        "chart_type": "bar",
        "default_params": {"limit": 10},
    },
    {
        "id": "t3",
        "label": "城市分布",
        "question": "城市分布",
        "description": "按城市统计场景数TOP20",
        "question_pattern": r"^(城市|城市分布|地市)\s*(TOP\d+|排名|分布)$",
        "intent_key": {"pattern": "top_n", "agg": "场景数", "condition_types": [], "dimension": "城市"},
        "sql_template": (
            "SELECT 城市, COUNT(DISTINCT 场景ID) AS 场景数 "
            "FROM data GROUP BY 城市 ORDER BY 场景数 DESC LIMIT {limit}"
        ),
        "chart_type": "bar",
        "default_params": {"limit": 20},
    },
    {
        "id": "t7",
        "label": "省份分布",
        "question": "省份分布",
        "description": "按省份统计场景数",
        "question_pattern": r"^(省份|省|省级)\s*(分布|排名|TOP\d+)?$",
        "intent_key": {"pattern": "distribution", "agg": "场景数", "condition_types": [], "dimension": "省份"},
        "sql_template": (
            "SELECT 省份, COUNT(DISTINCT 场景ID) AS 场景数 "
            "FROM data GROUP BY 省份 ORDER BY 场景数 DESC LIMIT {limit}"
        ),
        "chart_type": "bar",
        "default_params": {"limit": 31},
    },
    {
        "id": "t8",
        "label": "场景时长分布",
        "question": "场景时长分布",
        "description": "场景时长的分钟档分布",
        "question_pattern": r"^(场景时长|时长|时长分布)\s*(分布|统计)?$",
        "intent_key": {"pattern": "distribution", "agg": "场景数", "condition_types": [], "dimension": "场景时长档"},
        "sql_template": (
            "SELECT "
            "CASE "
            "WHEN 场景时长 < 60 THEN '1分钟以内' "
            "WHEN 场景时长 < 120 THEN '1-2分钟' "
            "WHEN 场景时长 < 180 THEN '2-3分钟' "
            "WHEN 场景时长 < 300 THEN '3-5分钟' "
            "WHEN 场景时长 < 600 THEN '5-10分钟' "
            "ELSE '10分钟以上' END AS 时长档位, "
            "COUNT(DISTINCT 场景ID) AS 场景数 "
            "FROM data GROUP BY 时长档位 ORDER BY 时长档位"
        ),
        "chart_type": "bar",
    },
    {
        "id": "t4",
        "label": "月度趋势",
        "question": "月度趋势",
        "description": "按月统计场景数趋势",
        "question_pattern": r"^(月度|月份|月)\s*(趋势|分布|统计)$",
        "intent_key": {"pattern": "trend", "agg": "场景数", "condition_types": [], "dimension": "月度"},
        "sql_template": (
            "SELECT strftime(ydate, '%Y-%m') AS 月份, COUNT(DISTINCT 场景ID) AS 场景数 "
            "FROM data GROUP BY 月份 ORDER BY 月份"
        ),
        "chart_type": "line",
    },
    # ===== 新增模板：带条件分布（疾病+药品） =====
    {
        "id": "tx01",
        "label": "疾病分布",
        "question": "各疾病分布",
        "description": "各疾病名称分布排名",
        "intent_key": {"pattern": "distribution", "agg": "场景数", "condition_types": [], "dimension": "疾病名称"},
        "sql_template": (
            "SELECT {dimension}, COUNT(DISTINCT 场景ID) AS 场景数 "
            "FROM data {conditions} "
            "GROUP BY {dimension} ORDER BY 场景数 DESC LIMIT {limit}"
        ),
        "chart_type": "bar",
    },
    {
        "id": "tx02",
        "label": "药品分布",
        "question": "各药品分布",
        "description": "各药品名称分布排名",
        "intent_key": {"pattern": "distribution", "agg": "场景数", "condition_types": [], "dimension": "场景提及药品"},
        "sql_template": (
            "SELECT {dimension}, COUNT(DISTINCT 场景ID) AS 场景数 "
            "FROM data {conditions} "
            "GROUP BY {dimension} ORDER BY 场景数 DESC LIMIT {limit}"
        ),
        "chart_type": "bar",
    },
    {
        "id": "tx03",
        "label": "疾病成交分布",
        "question": "疾病成交按城市分布",
        "description": "某疾病成交场景按维度分布",
        "intent_key": {"pattern": "distribution", "agg": "成交场景数", "condition_types": ["disease"], "dimension": "*"},
        "sql_template": (
            "SELECT {dimension}, COUNT(DISTINCT 场景ID) AS 成交场景数 "
            "FROM data WHERE 交易是否达成='是' {conditions_extra} "
            "GROUP BY {dimension} ORDER BY 成交场景数 DESC LIMIT {limit}"
        ),
        "chart_type": "bar",
    },
    {
        "id": "tx04",
        "label": "药品成交分布",
        "question": "药品成交分布",
        "description": "各药品成交场景数排名",
        "intent_key": {"pattern": "distribution", "agg": "成交场景数", "condition_types": [], "dimension": "场景提及药品"},
        "sql_template": (
            "SELECT {dimension}, COUNT(DISTINCT 场景ID) AS 成交场景数 "
            "FROM data WHERE 交易是否达成='是' {conditions_extra} "
            "GROUP BY {dimension} ORDER BY 成交场景数 DESC LIMIT {limit}"
        ),
        "chart_type": "bar",
    },
    {
        "id": "tx05",
        "label": "疾病成交率分布",
        "question": "疾病成交率按省份",
        "description": "某疾病成交率按维度分布",
        "intent_key": {"pattern": "distribution", "agg": "成交率", "condition_types": ["disease"], "dimension": "*"},
        "sql_template": (
            "SELECT {dimension}, "
            "ROUND(COUNT(DISTINCT CASE WHEN 交易是否达成='是' THEN 场景ID END) * 100.0 "
            "/ COUNT(DISTINCT 场景ID), 1) AS 成交率 "
            "FROM data {conditions} "
            "GROUP BY {dimension} ORDER BY 成交率 DESC LIMIT {limit}"
        ),
        "chart_type": "bar",
    },
    # ===== 趋势模板（带条件） =====
    {
        "id": "tt01",
        "label": "疾病月度趋势",
        "question": "疾病月度趋势",
        "description": "某疾病月度场景数趋势",
        "intent_key": {"pattern": "trend", "agg": "场景数", "condition_types": ["disease"], "dimension": "月度"},
        "sql_template": (
            "SELECT strftime(ydate, '%Y-%m') AS 月份, COUNT(DISTINCT 场景ID) AS 场景数 "
            "FROM data {conditions} "
            "GROUP BY 月份 ORDER BY 月份"
        ),
        "chart_type": "line",
    },
    {
        "id": "tt02",
        "label": "疾病成交月度趋势",
        "question": "疾病成交月度趋势",
        "description": "某疾病月度成交场景数趋势",
        "intent_key": {"pattern": "trend", "agg": "成交场景数", "condition_types": ["disease"], "dimension": "月度"},
        "sql_template": (
            "SELECT strftime(ydate, '%Y-%m') AS 月份, COUNT(DISTINCT 场景ID) AS 成交场景数 "
            "FROM data WHERE 交易是否达成='是' {conditions_extra} "
            "GROUP BY 月份 ORDER BY 月份"
        ),
        "chart_type": "line",
    },
    # ===== 药品分布（特定维度的通用匹配） =====
    {
        "id": "tx06",
        "label": "药品-城市分布",
        "question": "药品城市分布",
        "description": "某药品按城市分布",
        "intent_key": {"pattern": "distribution", "agg": "场景数", "condition_types": ["drug_any"], "dimension": "城市"},
        "sql_template": (
            "SELECT 城市, COUNT(DISTINCT 场景ID) AS 场景数 "
            "FROM data {conditions} "
            "GROUP BY 城市 ORDER BY 场景数 DESC LIMIT {limit}"
        ),
        "chart_type": "bar",
    },
    {
        "id": "tx07",
        "label": "药品-省份分布",
        "question": "药品省份分布",
        "description": "某药品按省份分布",
        "intent_key": {"pattern": "distribution", "agg": "场景数", "condition_types": ["drug_any"], "dimension": "省份"},
        "sql_template": (
            "SELECT 省份, COUNT(DISTINCT 场景ID) AS 场景数 "
            "FROM data {conditions} "
            "GROUP BY 省份 ORDER BY 场景数 DESC LIMIT {limit}"
        ),
        "chart_type": "bar",
    },
    # ===== 药品+成交分布 =====
    {
        "id": "tx08",
        "label": "药品成交-城市分布",
        "question": "药品成交城市分布",
        "description": "某药品成交按城市分布",
        "intent_key": {"pattern": "distribution", "agg": "成交场景数", "condition_types": ["drug_any"], "dimension": "城市"},
        "sql_template": (
            "SELECT 城市, COUNT(DISTINCT 场景ID) AS 成交场景数 "
            "FROM data WHERE 交易是否达成='是' {conditions_extra} "
            "GROUP BY 城市 ORDER BY 成交场景数 DESC LIMIT {limit}"
        ),
        "chart_type": "bar",
    },
    {
        "id": "tx09",
        "label": "药品成交-省份分布",
        "question": "药品成交省份分布",
        "description": "某药品成交按省份分布",
        "intent_key": {"pattern": "distribution", "agg": "成交场景数", "condition_types": ["drug_any"], "dimension": "省份"},
        "sql_template": (
            "SELECT 省份, COUNT(DISTINCT 场景ID) AS 成交场景数 "
            "FROM data WHERE 交易是否达成='是' {conditions_extra} "
            "GROUP BY 省份 ORDER BY 成交场景数 DESC LIMIT {limit}"
        ),
        "chart_type": "bar",
    },
    # ===== 明细模板 =====
    {
        "id": "dl01",
        "label": "场景明细",
        "question": "场景明细",
        "description": "查询场景明细数据",
        "question_pattern": r"^(.+)?(明细|详情|逐条|罗列)(.+)?$",
        "intent_key": {"pattern": "detail", "agg": "明细", "condition_types": [], "dimension": None},
        "sql_template": "SELECT * FROM data {conditions} LIMIT {limit}",
        "chart_type": "table_only",
    },
]


class TemplateMatcher:
    """预置模板匹配器（v3：支持结构化意图匹配）"""

    # 药品条件类型家族（模板匹配层面归一化同族类型）
    _DRUG_CONDITION_ALIASES = {"drug_mentioned", "drug_named", "drug_ordered"}

    @staticmethod
    def _normalize_cond_types(types: list[str]) -> list[str]:
        """将药品细分条件类型归一化为 drug_any 家族

        在模板匹配层面，drug_mentioned/drug_named/drug_ordered
        应视为等价于 drug_any，避免因类型名不同导致模板错失。
        SQL 渲染仍使用原始精确类型，不损失精度。
        """
        result = []
        for t in types:
            if t in TemplateMatcher._DRUG_CONDITION_ALIASES:
                result.append("drug_any")
            else:
                result.append(t)
        return sorted(set(result))

    def match(self, question: str) -> Optional[dict]:
        """
        [保留] 旧式字符串正则匹配

        返回: {"template_id", "sql", "chart_type", ...} 或 None
        """
        q = question.strip()

        for tmpl in TEMPLATES:
            pattern = tmpl.get("question_pattern")
            if not pattern:
                continue
            m = re.match(pattern, q, re.IGNORECASE)
            if not m:
                continue

            params = m.groupdict() if m.groups() else {}
            defaults = tmpl.get("default_params", {})
            for k, v in defaults.items():
                params.setdefault(k, v)

            sql = tmpl["sql_template"].format(**params)
            return {
                "template_id": tmpl["id"],
                "label": tmpl["label"],
                "sql": sql,
                "chart_type": tmpl["chart_type"],
                "matched_question": tmpl["question"],
            }

        return None

    def match_by_intent(self, intent: QueryIntent) -> Optional[dict]:
        """
        基于结构化意图匹配模板。

        匹配策略：细粒度优先 + 通配符兜底
        - 先精确匹配 (pattern, agg, condition_types, dimension)
        - 再匹配 (pattern, agg, *, *)
        - 再匹配 (pattern, *, *, *)

        返回: {"template_id", "template_obj", "score"} 或 None
        """
        p = intent.query_pattern
        a = intent.agg
        # 提取 condition type 集合（归一化药品类型）
        raw_types = [c.type for c in intent.conditions]
        cond_types = self._normalize_cond_types(raw_types)
        d = intent.dimension

        candidates = []

        for tmpl in TEMPLATES:
            key = tmpl.get("intent_key")
            if not key:
                continue

            score = self._match_score(key, p, a, cond_types, d, tmpl)
            if score > 0:
                candidates.append((score, tmpl))

        if not candidates:
            return None

        # 按分数降序，选最高分
        # 按分数降序；同分时优先选有 {conditions} 占位符的模板
        # 避免 t7（无占位符）在包含药品条件时误胜 tx07（有占位符）
        candidates.sort(key=lambda x: (
            -x[0],                                          # 分数降序
            0 if '{conditions}' in x[1].get('sql_template', '') else 1  # 有条件占位符优先
        ))
        best = candidates[0]
        return {
            "template_id": best[1]["id"],
            "template_obj": best[1],
            "score": best[0],
        }

    def _match_score(
        self,
        key: dict,
        pattern: str,
        agg: str,
        cond_types: list[str],
        dimension: Optional[str],
        template: Optional[dict] = None,
    ) -> int:
        """
        计算模板匹配分数（分数越高越精确）

        完全匹配 100 分，每差一层减分
        """
        score = 0

        # 1. pattern 匹配（distribution ↔ top_n 互认：都是"按维度排序展示"）
        key_pattern = key["pattern"]
        if key_pattern == pattern:
            score += 40
        elif {key_pattern, pattern} <= {"distribution", "top_n"}:
            # distribution 和 top_n 在按维度排序展示时语义等价
            score += 30  # 略低于精确匹配
        else:
            return 0

        # 2. agg 精确匹配 +40，通配符 +20
        if key["agg"] == agg:
            score += 40
        else:
            return 0  # agg 必须匹配

        # 3. condition_types 精确匹配 +15，模板无条件且意图也无条件 +15
        key_cond_types = sorted(key.get("condition_types", []))
        if key_cond_types == cond_types:
            score += 15
        elif not key_cond_types and not cond_types:
            score += 15
        # 模板有【特定】条件类型且意图有该类型（允许意图有额外条件）
        elif key_cond_types and any(ct in cond_types for ct in key_cond_types):
            score += 8

        # 4. dimension 精确匹配 +15，通配符(*) +8，都不指定 +8，不匹配 0
        key_dim = key.get("dimension")
        if key_dim == dimension and dimension is not None:
            score += 15
        elif key_dim == "*":
            score += 8  # 通配维度：需要渲染器填充
        elif key_dim is None and dimension is None:
            score += 8
        elif key_dim is None and dimension is not None:
            score += 0  # 维度不匹配

        # 5. 【关键修复】意图有条件但模板无 {conditions} 占位符 → 减分
        #    避免特定场景时长分布(t8)、省份分布(t7)等硬编码模板
        #    在有 geo/disease/drug 条件时错误匹配，导致条件丢失
        if template and template.get("sql_template"):
            has_no_placeholder = '{conditions}' not in template["sql_template"]
        else:
            has_no_placeholder = True
        has_conditions = len(cond_types) > 0
        if has_conditions and has_no_placeholder:
            score -= 30  # 大幅减分，让有 {conditions} 的模板或 LLM fallback 胜出

        return score

    def get_similar_templates(
        self, intent: QueryIntent, top_k: int = 2
    ) -> list[dict]:
        """
        获取最相似的模板（供 LLM 兜底时作为参考示例）。

        按 match_score 排序取 top_k。
        """
        p = intent.query_pattern
        a = intent.agg
        # 提取 condition type 集合（归一化药品类型）
        raw_types = [c.type for c in intent.conditions]
        cond_types = self._normalize_cond_types(raw_types)
        d = intent.dimension

        scored = []
        for tmpl in TEMPLATES:
            key = tmpl.get("intent_key")
            if not key:
                continue
            score = self._match_score(key, p, a, cond_types, d, tmpl)
            if score > 0:
                scored.append((score, tmpl))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "id": s[1]["id"],
                "label": s[1]["label"],
                "question": s[1]["question"],
                "sql_template": s[1]["sql_template"],
                "score": s[0],
            }
            for s in scored[:top_k]
        ]

    def get_all_templates(self) -> list[dict]:
        """获取常用查询模板列表（供前端按钮展示）

        只返回自包含的模板（不需要额外参数），用于前端"常用查询"按钮。
        需要用户指定疾病/药品的模板（condition_types 非空）不包含在内，
        这些模板仅用于意图匹配时的内部路由。
        """
        return [
            {
                "id": t["id"],
                "label": t["label"],
                "question": t["question"],
                "description": t.get("description", ""),
            }
            for t in TEMPLATES
            if not t.get("intent_key", {}).get("condition_types", [])
        ]


matcher = TemplateMatcher()
