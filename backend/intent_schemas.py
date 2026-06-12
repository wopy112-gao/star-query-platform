"""星宝语料场景查询系统 — 意图结构定义

包含查询意图的完整枚举体系和结构化数据类。
"""

from __future__ import annotations
import json
import hashlib
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


class QueryPattern(str, Enum):
    """查询模式"""
    SINGLE_STAT = "single_stat"       # 单值统计：场景数、成交率、问症率
    DISTRIBUTION = "distribution"     # 按维度分布：各省场景数
    TOP_N = "top_n"                   # TOP排行：疾病TOP10
    RANKING = "ranking"               # 全量排名：所有省份排名（不限量）
    DETAIL = "detail"                 # 明细：逐条数据
    TREND = "trend"                   # 时间趋势：月度变化
    COMPARISON = "comparison"         # 对比：两种疾病/药品对比
    RATIO = "ratio"                   # 占比：某类占总量的百分比
    CORRELATION = "correlation"       # 关联分析

    @classmethod
    def values(cls) -> list[str]:
        return [e.value for e in cls]

    @classmethod
    def from_value(cls, v: str) -> Optional["QueryPattern"]:
        for e in cls:
            if e.value == v:
                return e
        return None


class Aggregation(str, Enum):
    """聚合方式"""
    SCENE_COUNT = "场景数"               # COUNT(DISTINCT 场景ID)
    DEAL_COUNT = "成交场景数"            # + 成交条件
    NODEAL_COUNT = "未成交场景数"        # + 未成交条件
    DEAL_RATE = "成交率"                # 成交/总
    NODEAL_RATE = "未成交率"            # 未成交/总
    SCENE_PCT = "场景占比"              # 子集/全量 × 100
    DURATION_AVG = "平均场景时长"       # AVG(场景时长)
    DURATION_MEDIAN = "场景时长中位数"  # PERCENTILE_CONT(0.5)
    COMBINATION_RATE = "联合用药率"     # 联合用药/总
    INQUIRY_RATE = "问症率"            # 问症/总
    KEYINFO_RATE = "关键信息到达率"     # 关键信息/总
    RECOMMEND_RATE = "活动推荐率"       # 活动推荐/总
    PARTICIPATE_RATE = "活动参与率"     # 活动参与/总
    DEDUP_COUNT = "去重计数"            # COUNT(DISTINCT {dedup_field}) — 动态字段去重
    MAX = "最大值"
    MIN = "最小值"
    SUM = "总和"
    RANK = "排名"
    DETAIL = "明细"                     # SELECT *

    @classmethod
    def values(cls) -> list[str]:
        return [e.value for e in cls]

    @classmethod
    def from_value(cls, v: str) -> Optional["Aggregation"]:
        for e in cls:
            if e.value == v:
                return e
        return None


class ConditionType(str, Enum):
    """条件类型"""
    DISEASE = "disease"                 # 疾病名称 LIKE
    DRUG_ANY = "drug_any"               # 三个药品字段任一匹配
    DRUG_NAMED = "drug_named"           # 顾客点名药品
    DRUG_MENTIONED = "drug_mentioned"   # 场景提及药品
    DRUG_ORDERED = "drug_ordered"       # 订单药品
    DEAL_YES = "deal_yes"               # 成交
    DEAL_NO = "deal_no"                 # 未成交
    NAMED_NOT_ORDERED = "named_not_ordered"            # 点名未成交
    MENTIONED_NOT_NAMED = "mentioned_not_named"        # 提及未点名
    MENTIONED_NOT_NAMED_ORDERED = "mn_no_named_ordered"  # 提及未点名又成交
    STORE_TYPE = "store_type"           # 连锁/门店类型
    GENDER = "gender"                   # 顾客性别
    COMBINATION = "combination"         # 联合用药
    ACTIVE_RECOMMEND = "active_recommend"      # 活动推荐
    ACTIVE_PARTICIPATE = "active_participate"  # 活动参与
    TIME_RANGE = "time_range"           # 日期范围
    GEO = "geo"                         # 地域过滤（省份/城市）
    TRUST = "trust"                     # 顾客信任度
    # ↓↓↓ 2026-06-11 新增条件类型 ↓↓↓
    CLERK_MENTION = "clerk_mention"           # 店员提及药品
    CLERK_RECOMMEND = "clerk_recommend"       # 店员推荐药品
    DRUG_RECOMMEND = "drug_recommend"         # 店员推荐药品（别名）
    AGE_LAYER = "age_layer"                   # 用药人年龄分层
    COMBO_ACTION = "combo_action"             # 联合用药动作（实际行为）
    COMMERCIAL = "commercial"                 # 是否商用数据
    CONFIDENCE = "confidence"                 # 业务置信度过滤（业务置信度='高'/'中'/'低'）

    @classmethod
    def values(cls) -> list[str]:
        return [e.value for e in cls]

    @classmethod
    def from_value(cls, v: str) -> Optional["ConditionType"]:
        for e in cls:
            if e.value == v:
                return e
        return None


class Dimension(str, Enum):
    """维度枚举"""
    PROVINCE = "省份"
    CITY = "城市"
    DISEASE_NAME = "疾病名称"
    STORE = "门店"
    CHAIN = "连锁"
    GENDER = "顾客性别"
    AGE = "顾客年龄"
    TRUST = "顾客信任度"
    MONTHLY = "月度"
    DURATION_BIN = "场景时长档"
    INQUIRY = "是否问症"
    COMBINATION_REASON = "联合用药合理性"
    ACTIVE_SATISFACTION = "活动满意度"
    STORE_TYPE = "门店类型"
    DRUG_MENTIONED = "场景提及药品"       # 场景中提到的药品（JSON数组UNNEST展开）
    DRUG_ORDERED = "订单药品"             # 实际成交的药品（JSON数组UNNEST展开）
    DRUG_NAMED = "顾客点名药品"           # 顾客点名要的药品（JSON数组UNNEST展开）
    # ↓↓↓ 2026-06-11 新增维度 ↓↓↓
    AGE_LAYER = "用药人年龄分层"
    COMBO_ACTION = "联合用药动作"
    COMMERCIAL = "是否商用"
    CONFIDENCE_SCORE = "综合置信度评分"
    CLERK_MENTIONED_DRUGS = "店员提及药品JSON"
    CLERK_RECOMMENDED_DRUGS = "店员推荐药品JSON"
    BUSINESS_CONFIDENCE = "业务置信度"
    SCENE_COMPLETENESS = "场景完整度"
    CUT_CONFIDENCE_SCORE = "切割置信度分值"
    CUT_COMPLETENESS_SCORE = "切割完整度分值"
    ACTIVITY_TIME_RATIO = "活动时间占比"

    @classmethod
    def values(cls) -> list[str]:
        return [e.value for e in cls]

    @classmethod
    def from_value(cls, v: str) -> Optional["Dimension"]:
        for e in cls:
            if e.value == v:
                return e
        return None


@dataclass
class Condition:
    """单个查询条件"""
    type: str         # ConditionType value
    value: str        # 条件值，如"感冒"、"雷诺考特"
    relation: str = "AND"  # 条件关系，目前只用 AND

    def to_dict(self) -> dict:
        return {"type": self.type, "value": self.value, "relation": self.relation}


@dataclass
class QueryIntent:
    """用户的查询意图（结构化表示）"""
    raw_question: str                          # 原始问题
    query_pattern: str                         # QueryPattern value
    agg: str                                   # Aggregation value
    conditions: list[Condition] = field(default_factory=list)  # 条件列表
    dimension: Optional[str] = None            # 分组维度（Dimension value 或空）
    limit: int = 50                            # 限制行数
    is_deal_filtered: bool = False             # 是否已自动处理成交过滤
    dedup_field: Optional[str] = None          # 去重字段名（当 agg='去重计数' 时生效）

    def to_dict(self) -> dict:
        d = {
            "query_pattern": self.query_pattern,
            "agg": self.agg,
            "conditions": [c.to_dict() for c in self.conditions],
            "dimension": self.dimension,
            "limit": self.limit,
        }
        if self.dedup_field:
            d["dedup_field"] = self.dedup_field
        return d

    @property
    def cache_key(self) -> str:
        """基于结构化内容生成缓存 key

        相比基于原始问题字符串的 key，结构化 key 有以下优势：
        1. 同语义不同表述 → 同一 key（"感冒各省"和"按省份展示感冒"拆解后一样）
        2. 维度精确 → 不会把"按省份"和"按城市"混为一个 key
        3. 可调试 → 能看到 key 中的条件组合
        """
        relevant = {
            "pattern": self.query_pattern,
            "agg": self.agg,
            "dedup_field": self.dedup_field,
            "conditions": sorted(
                [(c.type, c.value) for c in self.conditions],
                key=lambda x: x[0],
            ),
            "dimension": self.dimension,
            "limit": self.limit,
        }
        return hashlib.md5(json.dumps(relevant, sort_keys=True).encode()).hexdigest()

    @classmethod
    def from_dict(cls, d: dict, raw_question: str = "") -> "QueryIntent":
        """从字典构建 QueryIntent（带兼容性校验）"""
        return cls(
            raw_question=raw_question,
            query_pattern=d.get("query_pattern", "single_stat"),
            agg=d.get("agg", "场景数"),
            conditions=[
                Condition(**{k: c[k] for k in ('type', 'value', 'relation') if k in c}) if isinstance(c, dict) else c
                for c in d.get("conditions", [])
            ],
            dimension=d.get("dimension"),
            limit=d.get("limit", 50),
            dedup_field=d.get("dedup_field"),
        )


# ============================================================
# 意图拆解器的返回结构
# ============================================================

@dataclass
class IntentResult:
    """意图拆解结果"""
    success: bool
    intent: Optional[QueryIntent] = None
    error: Optional[str] = None
    raw_llm_response: Optional[str] = None
    elapsed_ms: float = 0.0
