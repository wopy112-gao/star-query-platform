"""星宝语料场景查询系统 — 意图拆解器（L2 → L2.5）

将用户自然语言问题拆解为结构化意图（QueryIntent）。
调用 DeepSeek API，与 llm_translator 共用同一 API 配置。

输入：用户问题
输出：结构化意图（QueryPattern + Aggregation + Conditions + Dimension）

v4 改造内容：
1. 移除硬编码条件类型列表，改为 DDL 驱动
2. 新增 time_range 条件类型支持
3. 新增自动检测遗漏时间条件的安全兜底
"""

import json
import re
import time
from typing import Optional
from urllib import request as urllib_request
from urllib.error import URLError

from config import settings
from intent_schemas import (
    QueryIntent,
    IntentResult,
    Condition,
    QueryPattern,
    Aggregation,
    ConditionType,
    Dimension,
)
from schema_ddl import get_ddl_string

# ============================================================

from schema_ddl import get_ddl_string


SYSTEM_PROMPT = """你是一个医药零售数据的意图分析专家。
请将用户的查询问题解析为结构化 JSON，不要输出其他内容。

## 数据库结构
{ddl}

## 条件设计指南
根据 DDL 中的字段和用户问题，选择最合适的条件类型和字段值：

- **疾病/症状** → type="disease", 在 疾病名称 字段上用 LIKE 模糊匹配
- **药品名**（点名/提及/成交任一） → type="drug_any", 在 顾客点名药品/场景提及药品/订单药品 上用 CONTAINS
- **地域**（省份/城市） → type="geo", 在 省份 或 城市 字段上精确匹配
- **时间范围**（最近N天、近N天、本月、本周、昨天、2026年等） → type="time_range", value 写原始描述（如"最近7天""本月""2026年"）
- **成交/未成交** → type="deal_yes"/"deal_no", 用 交易是否达成 字段
- **联合用药（AI判断）** → type="combination", 用 是否联合用药 字段
- **顾客性别** → type="gender"
- **活动推荐/参与** → type="active_recommend"/"active_participate"
- **店员提及药品** → type="clerk_mention", 在 店员提及药品JSON 上用 LIKE 匹配
- **店员推荐药品** → type="clerk_recommend", 在 店员推荐药品JSON 上用 LIKE 匹配
- **用药人年龄分层** → type="age_layer", 在 用药人年龄分层 上精确匹配（如"老年人""青壮年"）
- **联合用药动作（店员实际行为）** → type="combo_action", 用 联合用药动作 字段（区别于AI判断的'是否联合用药'）
- **商用数据过滤** → type="commercial", 筛选用 是否商用='1' 的优质数据
- **业务置信度**（高/中/低） → type="confidence", 在 业务置信度 字段上精确匹配，如条件值'高'
- **售药推荐分析** → type="drug_recommend", 在 店员推荐药品JSON 上用 LIKE 匹配
- ⚠️ **如果用户问题中的限定词无法映射到上述类型，自由创建一个合理的 type 名，只要在 SQL 中能正确表达即可。**

## 聚合方式（agg）可用值
场景数 | 成交场景数 | 未成交场景数 | 成交率 | 未成交率 | 场景占比
平均场景时长 | 场景时长中位数
联合用药率 | 问症率 | 关键信息到达率 | 活动推荐率 | 活动参与率
最大值 | 最小值 | 总和 | 排名 | 明细

## 查询模式（query_pattern）
single_stat: 单值统计（场景数、成交率等单个数值）
distribution: 按维度分布
top_n: TOP排行
trend: 时间趋势（月度变化）
comparison: 对比
ratio: 占比
detail: 明细

## 维度（dimension）
省份 | 城市 | 疾病名称 | 门店 | 连锁 | 月度
顾客性别 | 顾客年龄 | 顾客信任度 | 场景时长档
场景提及药品 | 订单药品 | 顾客点名药品
用药人年龄分层 | 联合用药动作 | 是否商用 | 综合置信度评分
店员提及药品JSON | 店员推荐药品JSON
业务置信度 | 场景完整度 | 切割置信度分值 | 切割完整度分值 | 活动时间占比
（"不同产品/各产品" → dimension=场景提及药品，不要在 conditions 里加）
（"不同年龄段/各年龄段" → dimension=用药人年龄分层）
（"不同置信度/各置信度" → dimension=业务置信度）

## 输出格式（严格 JSON，不要其他文本）
{{
  "query_pattern": "single_stat",
  "agg": "场景数",
  "conditions": [],
  "dimension": null,
  "limit": 50
}}

## 注意事项
- 疾病和药品同时出现时，作为两个独立 condition
- 如果问题没有明确条件，conditions 为空数组
- 没有明确聚合方式时默认"场景数"
- 不涉及分布/排行时 dimension 为 null
- ⚠️ "疾病""药品""场景""门店"等泛指词不要作为 conditions 的具体值
- ⚠️ 但"产品"不是泛指词——用户说"各产品/不同产品"时是维度指示，dimension=场景提及药品
- ⚠️ 时间范围（最近N天、近N天、本月等）必须作为 condition 输出，不要遗漏"""


def _call_llm(messages: list[dict]) -> Optional[str]:
    """调用 DeepSeek API（与 llm_translator 相同的调用方式）"""
    api_key = settings.LLM_API_KEY
    if not api_key:
        return None

    url = f"{settings.LLM_BASE_URL.rstrip('/')}/v1/chat/completions"
    payload = json.dumps({
        "model": settings.LLM_MODEL,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 300,
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
        print(f"[IntentLLM] 请求失败: {e}")
        return None
    except (KeyError, json.JSONDecodeError) as e:
        print(f"[IntentLLM] 解析响应失败: {e}")
        return None


def _extract_json(llm_response: str) -> Optional[dict]:
    """从 LLM 回复中提取 JSON"""
    # 尝试 ```json ... ``` 代码块
    code_blocks = re.findall(
        r"```(?:json)?\s*\n?(.*?)\n?```", llm_response, re.DOTALL
    )
    if code_blocks:
        try:
            return json.loads(code_blocks[0].strip())
        except json.JSONDecodeError:
            pass

    # 尝试直接解析整个回复
    try:
        return json.loads(llm_response.strip())
    except json.JSONDecodeError:
        pass

    return None


def _validate_intent_json(raw: dict) -> Optional[dict]:
    """校验意图 JSON 的字段合法性

    检查所有枚举值是否在合法范围内。
    校验不通过返回 None（触发重试）。
    """
    # 检查必要字段
    for key in ("query_pattern", "agg"):
        if key not in raw:
            print(f"[IntentLLM] 字段缺失: {key}")
            return None

    # 校验 query_pattern
    if raw["query_pattern"] not in QueryPattern.values():
        print(f"[IntentLLM] 非法 query_pattern: {raw['query_pattern']}")
        return None

    # 校验 agg
    if raw["agg"] not in Aggregation.values():
        # 先尝试匹配「去重XXX数量」或「去重XXX」模式
        m = re.match(r'^去重(.+?)(?:数量|数目|的个数|的数目)?$', raw["agg"])
        if m:
            field = m.group(1)
            field_map = {
                "店员ID": "店员ID", "店员": "店员ID",
                "门店ID": "门店ID", "门店": "门店ID",
                "药师ID": "药师ID", "药师": "药师ID",
                "顾客ID": "顾客ID", "顾客": "顾客ID",
                "场景ID": "场景ID",
                "疾病名称": "疾病名称", "疾病": "疾病名称",
            }
            if field in field_map:
                raw["agg"] = "去重计数"
                raw["dedup_field"] = field_map[field]
                print(f"[IntentLLM] 识别到去重字段: {field} → {field_map[field]}")
            else:
                print(f"[IntentLLM] 未知的去重字段: {field}，降级处理")
                return None
        else:
            print(f"[IntentLLM] 非法 agg: {raw['agg']}")
            return None

    # 校验 conditions
    unknown_types = []
    for cond in raw.get("conditions", []):
        if cond.get("type") not in ConditionType.values():
            print(f"[IntentLLM] 发现未知 condition type: {cond.get('type')}（接受不拒绝）")
            unknown_types.append(cond.get("type"))
        if "value" not in cond or not cond["value"]:
            print(f"[IntentLLM] condition 缺少 value: {cond}")
            return None

    # 校验 dimension（可选字段）
    dim = raw.get("dimension")
    if dim is not None and dim != "null" and dim not in Dimension.values():
        print(f"[IntentLLM] 非法 dimension: {dim}")
        return None

    raw["_unknown_types"] = unknown_types
    return raw


def translate(question: str) -> IntentResult:
    """
    将自然语言问题拆解为结构化意图

    流程：
    1. 调用 DeepSeek API 做意图拆解
    2. 校验返回的 JSON 结构合法性
    3. 校验不通过 → 重试 1 次（带矫正提示）
    4. 重试仍失败 → 返回 error（触发调用方降级）
    5. 后处理：泛指词过滤 → 地域修正 → 实体计数修正 → 时间范围解析 → 自动兜底

    返回 IntentResult（成功/失败 + 结构化意图 + 元信息）
    """
    api_key = settings.LLM_API_KEY
    if not api_key:
        return IntentResult(
            success=False,
            error="LLM 未配置，无法进行意图拆解",
        )

    ddl = get_ddl_string()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(ddl=ddl)},
        {"role": "user", "content": f"请解析以下查询问题的意图：\n{question}"},
    ]

    start = time.time()

    # 第一轮调用
    response = _call_llm(messages)
    elapsed = round((time.time() - start) * 1000, 2)

    if response is None:
        return IntentResult(
            success=False,
            error="LLM 调用失败，请检查 API Key 和网络连接",
            elapsed_ms=elapsed,
        )

    raw_json = _extract_json(response)
    if raw_json is None:
        # 第一轮 JSON 解析失败 → 重试
        print(f"[IntentLLM] 第一轮 JSON 解析失败, response: {response[:100]}...")
        retry_messages = messages + [
            {"role": "assistant", "content": response},
            {
                "role": "user",
                "content": (
                    "请只输出严格的 JSON，不要包含任何其他文本、代码块标记或解释。"
                    f"\n请重新解析：{question}"
                ),
            },
        ]
        start2 = time.time()
        response2 = _call_llm(retry_messages)
        elapsed2 = round((time.time() - start2) * 1000, 2)
        elapsed = round(elapsed + elapsed2, 2)

        if response2:
            raw_json = _extract_json(response2)

        if raw_json is None:
            return IntentResult(
                success=False,
                error="意图拆解失败：无法从 LLM 回复中提取 JSON",
                raw_llm_response=response2 or response,
                elapsed_ms=elapsed,
            )

    # 校验 JSON 合法性
    validated = _validate_intent_json(raw_json)
    if validated is None:
        # 校验不通过 → 重试（带矫正）
        print(f"[IntentLLM] 第一轮校验失败, raw_json: {raw_json}")
        retry_msg = {"role": "user", "content": (
            f"以上输出有非法字段值，请修正。\n"
            f"合法 query_pattern: {QueryPattern.values()}\n"
            f"合法 agg: {Aggregation.values()}\n"
            f"合法 condition type: {ConditionType.values()}\n"
            f"合法 dimension: {Dimension.values()}\n"
            f"请重新解析并只输出合法 JSON：{question}"
        )}
        retry_messages = messages + [
            {"role": "assistant", "content": response},
            retry_msg,
        ]
        start3 = time.time()
        response3 = _call_llm(retry_messages)
        elapsed3 = round((time.time() - start3) * 1000, 2)
        elapsed = round(elapsed + elapsed3, 2)

        if response3:
            raw_json2 = _extract_json(response3)
            if raw_json2:
                validated = _validate_intent_json(raw_json2)

        if validated is None:
            return IntentResult(
                success=False,
                error="意图拆解校验失败",
                raw_llm_response=response3 or response,
                elapsed_ms=elapsed,
            )

    # 构建 QueryIntent
    intent = QueryIntent(
        raw_question=question,
        query_pattern=validated["query_pattern"],
        agg=validated["agg"],
        conditions=[
            Condition(**{k: c[k] for k in ('type', 'value', 'relation') if k in c}) if isinstance(c, dict) else c
            for c in validated.get("conditions", [])
        ],
        dimension=validated.get("dimension") or None,
        limit=validated.get("limit", 50),
        dedup_field=validated.get("dedup_field"),
    )

    # ⭐ 方案B：未知条件类型 → 写入 incident
    unknown_types = validated.get("_unknown_types", [])
    if unknown_types:
        intent._unknown_types = unknown_types
        try:
            from incident_writer import write_incident
            write_incident(
                inc_type="unknown_condition_type",
                question=question,
                intent_info=intent.to_dict(),
                warnings=[f"LLM创建了未知条件类型: {t}" for t in unknown_types],
            )
            print(f"[IntentLLM] 已记录未知条件类型: {unknown_types}")
        except ImportError:
            print(f"[IntentLLM] incident_writer 不可用，跳过记录")
        except Exception as e:
            print(f"[IntentLLM] 写入 incident 失败: {e}")

    # ========== 后处理流水线 ==========

    # 后处理1：过滤泛指词
    intent = _filter_generic_conditions(intent)

    # 后处理2：修正地域识别错误
    intent = _fix_geo_conditions(intent, question)

    # 后处理3：修正实体计数
    intent = _fix_entity_counting(intent, question)

    # 后处理4：自动判断成交口径
    deal_keywords = ["成交", "达成", "售卖"]
    if any(kw in question for kw in deal_keywords):
        intent.is_deal_filtered = True

    # 后处理5：解析并标准化时间范围条件
    intent = _fix_time_range(intent)

    # 后处理6：自动检测遗漏的时间条件（LLM 兜底）
    intent = _auto_detect_time_range(intent, question)

    return IntentResult(
        success=True,
        intent=intent,
        raw_llm_response=response,
        elapsed_ms=elapsed,
    )


# ============================================================
# 条件后处理：泛指词过滤
# ============================================================

GENERIC_KEYWORDS = {"疾病", "药品", "药品类型", "场景", "门店", "品类"}
GENERIC_DIMENSIONS = {"疾病", "疾病名称", "药品", "药品类型", "场景", "门店", "品类", "产品", "商品"}


def _filter_generic_conditions(intent: QueryIntent) -> QueryIntent:
    """过滤泛指词条件

    用户说"疾病月度趋势"时意图拆解器可能把"疾病"当成具体值。
    这类泛指词应该被过滤掉，因为用户没有指定具体疾病/药品名。

    同时清理 dimension 中的泛指维度（如 dim=疾病名称 而无具体疾病条件）。
    """
    filtered = [
        c for c in intent.conditions
        if c.value not in GENERIC_KEYWORDS
    ]
    if len(filtered) != len(intent.conditions):
        print(
            f"[IntentFilter] 过滤了 {len(intent.conditions) - len(filtered)} "
            f"个泛指条件: {[c.value for c in intent.conditions if c.value in GENERIC_KEYWORDS]}"
        )
    intent.conditions = filtered

    # 清理维度列表中的泛指维度
    if intent.dimension and intent.dimension in GENERIC_DIMENSIONS:
        # 合法维度枚举值 → 保留（如"疾病名称分布"是合法查询）
        if intent.dimension in Dimension.values():
            return intent

        # 检查是否有对应的具体 condition
        has_specific = any(
            c.value not in GENERIC_DIMENSIONS
            for c in intent.conditions
        )
        if not has_specific:
            print(
                f"[IntentFilter] 过滤泛指 dimension: {intent.dimension}"
                f"（用户未指定具体{intent.dimension}，清除）"
            )
            intent.dimension = None

    return intent


# ============================================================
# 条件后处理：地域修正
# ============================================================

def _fix_geo_conditions(intent: QueryIntent, question: str) -> QueryIntent:
    """修正 LLM 误判的省份/城市条件"""
    if not kb.is_loaded:
        kb.load()

    fixed_count = 0
    new_conditions = []
    for cond in intent.conditions:
        if cond.type == "geo":
            new_conditions.append(cond)
            continue
        if cond.type == "disease" and kb.is_geo_name(cond.value):
            print(f"[GeoFix] 修正误判: {cond.value} disease→geo")
            new_conditions.append(Condition(type="geo", value=cond.value))
            fixed_count += 1
            continue
        new_conditions.append(cond)

    # 补充遗漏的地域条件
    if fixed_count == 0:
        for prov in kb.province_names:
            if prov in question or (prov.endswith("省") and prov[:-1] in question):
                already_has = any(
                    c.value.lower() in (prov, prov[:-1]) for c in new_conditions
                )
                if not already_has:
                    print(f"[GeoFix] 补充遗漏的省份条件: {prov}")
                    new_conditions.append(Condition(type="geo", value=prov))
                    fixed_count += 1
                break
        if fixed_count == 0:
            for city in kb.city_names:
                if city in question and len(city) >= 2:
                    already_has = any(
                        c.value.lower() == city for c in new_conditions
                    )
                    if not already_has:
                        print(f"[GeoFix] 补充遗漏的城市条件: {city}")
                        new_conditions.append(Condition(type="geo", value=city))
                        fixed_count += 1
                    break

    if fixed_count > 0:
        intent.conditions = new_conditions

    return intent


# ============================================================
# 条件后处理：实体计数修正
# ============================================================

ENTITY_COUNT_MAP: dict[str, tuple[str, str]] = {
    "店员": ("店员ID", "店员数"),
    "药师": ("店员ID", "药师数"),
    "门店": ("门店ID", "门店数"),
    "药房": ("门店ID", "门店数"),
    "药店": ("门店ID", "门店数"),
    "连锁": ("连锁", "连锁数"),
    "城市": ("城市", "城市覆盖数"),
    "省份": ("省份", "省份数"),
    "省": ("省份", "省份数"),
}


def _fix_entity_counting(intent: QueryIntent, question: str) -> QueryIntent:
    """修正 LLM 的实体计数错误"""
    if intent.query_pattern != "single_stat":
        return intent
    if intent.agg not in ("场景数",):
        return intent

    for entity_kw, (count_field, _alias) in ENTITY_COUNT_MAP.items():
        if entity_kw in question:
            print(f"[EntityFix] 实体计数修正: '{entity_kw}' → COUNT(DISTINCT {count_field})")
            intent.agg = "去重计数"
            intent.dedup_field = count_field
            break

    return intent


# ============================================================
# 条件后处理：时间范围解析
# ============================================================

# 时间范围正则模式 → 标准化的条件 value
TIME_RANGE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"最近\s*(\d+)\s*天"), "最近{0}天"),
    (re.compile(r"近\s*(\d+)\s*天"), "最近{0}天"),
    (re.compile(r"最近\s*(\d+)\s*日"), "最近{0}天"),
    (re.compile(r"近\s*(\d+)\s*日"), "最近{0}天"),
    (re.compile(r"最近\s*(\d+)\s*周"), "最近{0}周"),
    (re.compile(r"近\s*(\d+)\s*周"), "最近{0}周"),
    (re.compile(r"最近\s*(\d+)\s*个?月"), "最近{0}个月"),
    (re.compile(r"近\s*(\d+)\s*个?月"), "最近{0}个月"),
    (re.compile(r"上个月|上月"), "上月"),
    (re.compile(r"本月|当月|这个月"), "本月"),
    (re.compile(r"本周|这周"), "本周"),
    (re.compile(r"今年|本年|全年"), "今年"),
    (re.compile(r"昨天|昨日"), "昨天"),
    (re.compile(r"今天|今日"), "今天"),
    (re.compile(r"近\s*半\s*年"), "最近6个月"),
    (re.compile(r"近\s*(\d+)\s*年"), "最近{0}年"),
    # 具体年月（前/后方向）：2026年3月以前/之前/以后/之后/后 → 保持原样
    (re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*个?月?份?\s*(以前|之前|以后|之后|后)"), None),
    # 具体年月：2026年1月（精确到月份）
    (re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*个?月?份?"), "{0}年{1}月"),
    # 具体年份：2026年、2025年等 → 放在年月后面避免提前匹配
    (re.compile(r"(\d{4})\s*年"), "{0}年"),
]


def _parse_time_range_text(text: str) -> str | None:
    """将时间范围的自然语言描述标准化"""
    text = text.strip().lower()
    for pattern, template in TIME_RANGE_PATTERNS:
        m = pattern.search(text)
        if m:
            if template is None:
                # template=None: 保持原样（如"2026年3月以前"→不改）
                return m.group(0)
            groups = m.groups()
            if groups:
                return template.format(*groups)
            else:
                return template
    return None


def _fix_time_range(intent: QueryIntent) -> QueryIntent:
    """解析并标准化时间范围条件"""
    new_conditions = []
    for cond in intent.conditions:
        if cond.type == "time_range":
            normalized = _parse_time_range_text(cond.value)
            if normalized:
                cond.value = normalized
                print(f"[TimeRange] 标准化: {cond.value} → {normalized}")
            new_conditions.append(cond)
        else:
            new_conditions.append(cond)

    intent.conditions = new_conditions
    return intent


# ============================================================
# 条件后处理：自动检测遗漏的时间条件（安全兜底）
# ============================================================

_TIME_KEYWORDS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"最近\s*\d+\s*天"), "time_range"),
    (re.compile(r"最近\s*\d+\s*日"), "time_range"),
    (re.compile(r"近\s*\d+\s*天"), "time_range"),
    (re.compile(r"近\s*\d+\s*日"), "time_range"),
    (re.compile(r"近\s*\d+\s*个?月"), "time_range"),
    (re.compile(r"最近\s*\d+\s*个?月"), "time_range"),
    (re.compile(r"本月|当月"), "time_range"),
    (re.compile(r"本周|这周"), "time_range"),
    (re.compile(r"今年|本年"), "time_range"),
    (re.compile(r"昨[天日]"), "time_range"),
    (re.compile(r"今[天日]"), "time_range"),
    (re.compile(r"上个月|上月"), "time_range"),
    (re.compile(r"季度"), "time_range"),
    (re.compile(r"同比|环比"), "time_range"),
    # 具体年份：2026年、2025年等
    (re.compile(r"\d{4}\s*年"), "time_range"),
    # X年Y月以前/以后：2026年3月以前、2026年1月份之后
    (re.compile(r"\d{4}\s*年\s*\d{1,2}\s*个?月?份?\s*(以前|之前|以后|之后|后)"), "time_range"),
]


def _auto_detect_time_range(intent: QueryIntent, question: str) -> QueryIntent:
    """自动检测遗漏的时间条件（安全兜底）

    如果用户问题中明显有时间范围关键词，
    但 LLM 输出的 conditions 中没有 time_range 条件，
    则自动补充。
    """
    if any(c.type == "time_range" for c in intent.conditions):
        return intent

    for pattern, _ in _TIME_KEYWORDS:
        if pattern.search(question):
            m = pattern.search(question)
            time_phrase = m.group(0) if m else question
            normalized = _parse_time_range_text(time_phrase)

            print(
                f"[TimeRange] 自动检测到时间范围（LLM遗漏）: "
                f"'{time_phrase}' → '{normalized or time_phrase}'"
            )

            intent.conditions.append(Condition(
                type="time_range",
                value=normalized or time_phrase,
            ))
            break

    return intent
