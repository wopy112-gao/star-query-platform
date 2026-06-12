# 星宝数据平台 — Prompt 拆解完整技术方案

> 文档状态：待审核
> 编写日期：2026-05-25
> 对应方向：优化方向三 — Prompt 拆解（完整版）

---

## 一、背景与问题

### 当前架构

```
用户问题 ──→ [巨型 System Prompt] ──→ LLM ──→ SQL
```

**System Prompt 当前注入内容：**
- 32 个字段说明
- 6+ 条业务规则
- 18 个 Few-shot 示例
- 30 个采样药品名
- 180+ 个疾病关键词

### 核心问题

| 问题 | 表现 | 后果 |
|------|------|------|
| 知识混在一起 | 字段、规则、示例、药品名同一层 | LLM 注意力分散，频繁遗漏或混淆维度 |
| 维度错配 | 问"省份"出"城市" | Group By 字段与用户意图不一致 |
| 药品名采样偏差 | 每次重启采样 30 个，可能不同 | 同一问题不同时间结果不同 |
| Few-shot 覆盖不足 | 新查询模式无对应示例 | LLM 自由发挥，输出不可控 |
| 语义缓存粗粒度 | 缓存 key = md5(问题字符串) | 同一语义不同表述无法命中 |

### 核心思路

> 把「一步翻译」拆成「意图拆解 → 查询匹配 → SQL 渲染」三步 Pipeline

---

## 二、完整架构

### 全链路数据流

```
用户问题
   │
   ▼
┌─────────────────────────────────────────────────┐
│ ① 意图拆解器 (query_intent.py)                 │
│   输入: "感冒各城市成交场景数"                    │
│   模型: DeepSeek（独立调用，与翻译同一模型）      │
│   输出: 结构化 JSON（意图表示）                  │
│   Prompt: ~200 tokens（仅意图拆解规则）          │
└─────────────────────┬───────────────────────────┘
                      │ 结构化JSON
                      ▼
┌─────────────────────────────────────────────────┐
│ ② 结构化缓存查询 (query_cache.py 改造)         │
│   新 Key = md5(结构化JSON)                      │
│   精确匹配 → 直接返回缓存 SQL 和结果             │
└─────────────────────┬───────────────────────────┘
                      │ 未命中
                      ▼
┌─────────────────────────────────────────────────┐
│ ③ 模板引擎匹配 (template_matcher.py 改造)      │
│   用 (query_pattern, agg, conditions, dimension) │
│   四元组匹配预定义 SQL 模板                      │
│   匹配 → 参数填充 → SQL (零 LLM 成本)           │
└─────────────────────┬───────────────────────────┘
                      │ 未匹配
                      ▼
┌─────────────────────────────────────────────────┐
│ ④ LLM 精简翻译 (llm_translator.py 改造)        │
│   输入: 结构化JSON + 相关字段说明               │
│   + 1-2 个最相似模板示例（按 query_pattern 取） │
│   Prompt: ~500 tokens（精简注入）               │
└─────────────────────┬───────────────────────────┘
                      │ SQL
                      ▼
┌─────────────────────────────────────────────────┐
│ ⑤ SQL 质量门禁 (sql_validator.py 新增)         │
│   - 语法层：SQL 语法错误 → 拦截重试              │
│   - 结构层：维度/条件/口径与意图不一致 → 拦截     │
│   - 结果层：0行或大量行 → 标注提醒             │
└─────────────────────┬───────────────────────────┘
                      │ 通过
                      ▼
┌─────────────────────────────────────────────────┐
│ ⑥ 执行 & 缓存写入                              │
│   执行 SQL → 返回结果 → 写入结构化缓存          │
└─────────────────────────────────────────────────┘
```

### 模块职责总览

| 模块 | 新建/改造 | 职责 |
|------|----------|------|
| `query_intent.py` | **新建** | 意图拆解器，LLM 调用 + JSON Schema 严格校验 |
| `query_cache.py` | **改造** | 缓存 key 从 md5(问题) → md5(结构化JSON)；缓存版本机制保留 |
| `template_matcher.py` | **改造** | 从匹配问题字符串 → 匹配四元组 `(pattern, agg, cond, dim)` 微调意图参数 |
| `sql_renderer.py` | **新建** | 模板参数填充，处理条件组合、维度嵌入、聚合公式 |
| `llm_translator.py` | **改造** | 精简 Prompt，只注入当前查询所需字段和示例 |
| `sql_validator.py` | **新建** | 三层校验：语法 → 结构逻辑 → 结果异常 |
| `query_router.py` | **改造** | 编排上述6个步骤的调用顺序 |
| `intent_schemas.py` | **新建** | 结构化意图所有枚举、Schema、条件类型定义 |

---

## 三、意图拆解器 (query_intent.py)

### 结构化意图 Schema

```python
# intent_schemas.py

from enum import Enum


class QueryPattern(str, Enum):
    """查询模式"""
    SINGLE_STAT = "single_stat"       # 单值统计：场景数、成交率、问症率
    DISTRIBUTION = "distribution"     # 按维度分布：各省场景数
    TOP_N = "top_n"                   # TOP排行：疾病TOP10
    RANKING = "ranking"               # 全量排名：所有省份排名
    DETAIL = "detail"                 # 明细：逐条数据
    TREND = "trend"                   # 时间趋势：月度变化
    COMPARISON = "comparison"         # 对比：两种疾病/药品对比
    RATIO = "ratio"                   # 占比：疾病占比
    CORRELATION = "correlation"       # 关联分析：联合用药 vs 问症


class Aggregation(str, Enum):
    """聚合方式"""
    SCENE_COUNT = "场景数"               # COUNT(DISTINCT 场景ID)
    DEAL_COUNT = "成交场景数"            # + 成交条件
    NODEAL_COUNT = "未成交场景数"        # + 未成交条件
    DEAL_RATE = "成交率"                # 成交/总
    NODEAL_RATE = "未成交率"            # 未成交/总
    RATE = "率"                         # 通用率
    SCENE_PCT = "场景占比"              # /总场景数 × 100
    SCENE_AVG = "平均场景数"            # AVG
    DURATION_AVG = "平均场景时长"       # AVG(场景时长)
    DURATION_MEDIAN = "场景时长中位数"  # PERCENTILE_CONT
    MAX = "最大值"
    MIN = "最小值"
    SUM = "总和"
    RANK = "排名"                       # RANK() OVER
    COMBINATION_RATE = "联合用药率"     # 联合用药/总
    INQUIRY_RATE = "问症率"            # 问症/总
    KEYINFO_RATE = "关键信息到达率"     # 关键信息/总
    RECOMMEND_RATE = "活动推荐率"       # 活动推荐/总
    PARTICIPATE_RATE = "活动参与率"     # 活动参与/总
    DETAIL = "明细"                     # SELECT *


class ConditionType(str, Enum):
    """条件类型（对应 SQL WHERE 子句的生成）"""
    DISEASE = "disease"                 # 疾病名称 LIKE
    DRUG_ANY = "drug_any"               # 三个药品字段任一匹配
    DRUG_NAMED = "drug_named"           # 顾客点名药品
    DRUG_MENTIONED = "drug_mentioned"   # 场景提及药品
    DRUG_ORDERED = "drug_ordered"       # 订单药品
    DEAL_YES = "deal_yes"               # 交易是否达成='是'
    DEAL_NO = "deal_no"                 # 交易是否达成='否'
    NAMED_NOT_ORDERED = "named_not_ordered"      # 点名未成交
    MENTIONED_NOT_NAMED = "mentioned_not_named"  # 提及未点名
    MENTIONED_NOT_NAMED_ORDERED = "mn_no_named_ordered"  # 提及未点名又成交
    STORE_TYPE = "store_type"           # 连锁 LIKE
    GENDER = "gender"                   # 顾客性别
    COMBINATION = "combination"         # 是否联合用药='是'
    AGE_GROUP = "age_group"             # 顾客年龄段
    ACTIVE_RECOMMEND = "active_recommend"    # 是否场景下活动推荐='是'
    ACTIVE_PARTICIPATE = "active_participate"  # 活动是否参与='是'
    TIME_RANGE = "time_range"           # 日期范围


class Dimension(str, Enum):
    """维度枚举"""
    PROVINCE = "省份"
    CITY = "城市"
    DISEASE = "疾病名称"
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
```

### 完整意图结构

```python
@dataclass
class QueryIntent:
    """用户的查询意图（结构化表示）"""
    raw_question: str                          # 原始问题
    query_pattern: QueryPattern                # 查询模式
    agg: Aggregation                           # 聚合方式
    conditions: list[dict]                     # 条件列表
    dimension: Optional[str] = None            # 分组维度
    limit: int = 50                            # 限制行数
    is_deal_filtered: bool = False             # 是否默认加成交过滤
    additional_info: dict = field(default_factory=dict)  # 额外上下文

    @property
    def cache_key(self) -> str:
        """基于结构化内容生成缓存 key，而非基于原始问题"""
        relevant = {
            "pattern": self.query_pattern.value,
            "agg": self.agg.value,
            "conditions": sorted(
                [(c["type"], c["value"]) for c in self.conditions],
                key=lambda x: x[0]
            ),
            "dimension": self.dimension,
            "limit": self.limit,
        }
        return hashlib.md5(json.dumps(relevant, sort_keys=True).encode()).hexdigest()
```

### 意图拆解 Prompt（精简版，~200 tokens）

```
你是一个医药零售数据的意图分析专家。
请将用户的查询问题解析为结构化 JSON。

可用条件类型：
- disease: 疾病筛选（如感冒、咳嗽）
- drug_any: 药品相关（三个药品字段任一匹配）
- drug_named: 顾客点名药品
- drug_mentioned: 场景提及药品
- drug_ordered: 订单药品
- named_not_ordered: 点名但未成交
- mentioned_not_named: 提及但未点名
- mn_no_named_ordered: 提及未点名又成交
- deal_yes: 成交
- deal_no: 未成交
- combination: 联合用药

可用查询模式：
- single_stat: 单值统计（场景数、成交率等）
- distribution: 按维度分布（各省、各城市）
- top_n: TOP排行
- trend: 时间趋势（月度等）
- comparison: 对比
- ratio: 占比计算
- detail: 场景明细

可用维度：
- 省份、城市、门店、连锁、疾病名称、月度
- 顾客性别、顾客年龄、顾客信任度、场景时长档

可用聚合方式：
- 场景数、成交场景数、成交率、未成交率、场景占比
- 联合用药率、问症率、关键信息到达率、平均场景时长
- 场景时长中位数、活动推荐率、活动参与率

输出格式（严格 JSON，不要其他文本）：
{
  "query_pattern": "distribution",
  "agg": "成交场景数",
  "conditions": [{"type": "disease", "value": "感冒", "relation": "AND"}],
  "dimension": "城市",
  "limit": 50
}

注意事项：
- relation 只用 "AND"，支持多条件叠加
- 如果问题涉及"场景提了但没点名"类描述，精确映射到对应条件类型
- 疾病和药品同时出现时，作为两个独立条件
- 不要推测用户未提到的维度和条件
```

### 意图拆解流程

```
query_intent.translate(question: str) -> QueryIntent

1. 调用 DeepSeek API（独立调用，同 llm_translator 的调用方式）
2. 接收 LLM 返回的 JSON
3. 用 Pydantic/JSON Schema 严格校验字段值
4. 校验不通过 → 重试 1 次（调整 Prompt 提醒）
5. 校验通过 → 返回 QueryIntent 对象
6. 极端情况（重试仍失败）→ 回退到原始 llm_translator（全量 Prompt 兜底）
```

---

## 四、结构化缓存 (query_cache.py 改造)

### 当前缓存机制

```python
# 当前：三层缓存
# L1: 内存 dict {md5(问题): SQL}
# L2: DuckDB 持久化表 query_cache {md5(问题): SQL, 结果}
# L3: 语义匹配 {问题: 相似问题 hash} → 当前维护两套缓存的逻辑
```

### 改造后

```python
# 改造后：结构化缓存，缓存 key = md5(结构化JSON)
# L1: 内存 dict {cache_key: {sql, result, timestamp}}
# L2: DuckDB 持久化表 structured_cache
#    {cache_key, intent_json, sql, result_json, created_at, hit_count}
# L3: 移除（语义匹配不再需要，因为结构化 key 天然支持同语义命中）
```

### 缓存命中逻辑

```
输入 QueryIntent → 计算 cache_key
  → 命中 L1 → 直接返回
  → 命中 L2 → 写回 L1 → 返回
  → 未命中 → 走模板/LLM → 写入 L1+L2
```

### 缓存自动失效

保留现有缓存版本机制（`CACHE_VERSION` + 代码 MD5 哈希），代码变更自动清空。

新 key 系统上线时，旧缓存在第一次请求时自动清理（因 key 格式不同，不会命中，随版本更新逐步淘汰）。

---

## 五、模板引擎 (template_matcher.py + sql_renderer.py)

### template_matcher.py 改造

**改造前：** 正则匹配用户问题字符串 → 生成 SQL
**改造后：** 匹配四元组 `(query_pattern, agg, conditions, dimension)` → 返回匹配的模板 ID

```python
class TemplateMatcher:
    """模板匹配器（改造后）"""

    def match_by_intent(self, intent: QueryIntent) -> Optional[Template]:
        """
        基于结构化意图匹配模板。

        匹配策略：细粒度优先 + 通配符兜底
        - 先精确匹配 (pattern, agg, cond_type_set, dim)
        - 再匹配 (pattern, agg, *, *)
        - 再匹配 (pattern, *, *, *)
        """

    def get_similar_templates(self, intent: QueryIntent, top_k: int = 2) -> list[Template]:
        """获取最相似的模板（供 LLM 兜底时作为示例参考）"""
```

### sql_renderer.py 新增

```python
class SQLRenderer:
    """
    SQL 渲染引擎。

    职责：
    1. 根据模板 ID + 意图参数 → 填充模板变量
    2. 处理条件组合（多条件的 AND 拼接）
    3. 处理维度字段替换
    4. 处理聚合公式替换
    """

    def render(self, template: Template, intent: QueryIntent) -> str:
        """
        渲染最终 SQL。
        流程：
        1. 加载模板的 SQL 骨架
        2. 替换 {conditions} 为意图中的条件 WHERE 子句
        3. 替换 {dimension} 为意图中的维度字段
        4. 替换 {agg} 为意图中的聚合公式
        5. 替换 {limit} 为意图中的行数限制
        """
```

### 模板格式（YAML 配置）

参见 [附录一：完整模板清单](#附录一完整模板清单)。

每个模板的结构：

```yaml
- id: TD01           # 模板唯一 ID
  pattern: distribution
  agg: 场景数
  conditions: []     # 空 = 无条件
  dimension: 省份
  sql: |
    SELECT {dimension} AS 维度,
           COUNT(DISTINCT 场景ID) AS 场景数
    FROM data
    {conditions}
    GROUP BY {dimension}
    ORDER BY 场景数 DESC
    LIMIT {limit}
  chart_type: bar
```

### 条件 SQL 映射表（renderer 内部维护）

```python
CONDITION_SQL_MAP = {
    "disease": "疾病名称 LIKE '%{value}%'",
    "drug_any": (
        "CONTAINS(顾客点名药品, '{value}') "
        "OR CONTAINS(场景提及药品, '{value}') "
        "OR CONTAINS(订单药品, '{value}')"
    ),
    "drug_named": "CONTAINS(顾客点名药品, '{value}')",
    "drug_mentioned": "CONTAINS(场景提及药品, '{value}')",
    "drug_ordered": "CONTAINS(订单药品, '{value}')",
    "deal_yes": "交易是否达成='是'",
    "deal_no": "交易是否达成='否'",
    "named_not_ordered": (
        "CONTAINS(顾客点名药品, '{value}') "
        "AND (交易是否达成='否' "
        "     OR NOT CONTAINS(订单药品, '{value}'))"
    ),
    "mentioned_not_named": (
        "CONTAINS(场景提及药品, '{value}') "
        "AND NOT CONTAINS(顾客点名药品, '{value}')"
    ),
    "mn_no_named_ordered": (
        "CONTAINS(场景提及药品, '{value}') "
        "AND NOT CONTAINS(顾客点名药品, '{value}') "
        "AND CONTAINS(订单药品, '{value}')"
    ),
    "combination": "是否联合用药='是'",
    "gender": "顾客性别='{value}'",
}
```

---

## 六、LLM 精简翻译 (llm_translator.py 改造)

### 改造后 Prompt 结构

```
系统：你是一个医药零售数据的 SQL 专家，负责生成 DuckDB SQL 查询。
已理解用户的意图（见用户消息中的结构化 JSON）。

只需生成 SQL，不要额外解释。
用 DuckDB 语法（兼容 PostgreSQL 子集）。
场景数必须用 COUNT(DISTINCT 场景ID)。
结果默认 LIMIT 50。

相关字段说明（仅注入当前查询涉及的字段）：
{仅注入 intent.conditions 和 intent.dimension 涉及的字段说明}

相似查询示例：
{注入 template_matcher.get_similar_templates(intent) 匹配到的 1-2 个示例}

===

用户：请为以下意图生成 SQL：
意图结构化：{intent_json}
原始问题：{raw_question}
```

### 改造前后对比

| 维度 | 改造前 | 改造后 |
|------|--------|--------|
| 字段说明 | 32 个全部注入 | 仅当前查询涉及的 2-5 个 |
| 业务规则 | 6+ 条全部注入 | 注入 2 条最相关的（场景数去重 + 成交口径） |
| Few-shot | 18 个全部注入 | 1-2 个按 query_pattern 筛选的最相似示例 |
| 药品列表 | 30 个采样注入 | 不注入（由条件匹配处理） |
| 疾病列表 | 180+ 全部注入 | 不注入 |
| Prompt 总大小 | ~3000 tokens | ~300-500 tokens |

### 兜底机制

如果意图拆解器失败（JSON 解析失败或重试仍失败），`llm_translator` **回退到原始全量 Prompt**，保证核心功能不中断：

```python
def translate(question: str, intent: Optional[QueryIntent] = None) -> dict:
    if intent:
        return _translate_with_intent(intent)
    else:
        return _translate_fallback(question)  # 回退到原始全量 Prompt
```

---

## 七、SQL 质量门禁 (sql_validator.py 新增)

### 三层校验

```python
class SQLValidator:
    """SQL 质量门禁校验器"""

    def validate(self, sql: str, intent: QueryIntent) -> ValidationResult:
        checks = [
            self._check_syntax(sql),          # 语法层
            self._check_structure(sql, intent),  # 结构逻辑层
        ]
        # 语法或结构校验失败 → 拦截
        for check in checks:
            if not check.passed:
                return ValidationResult(
                    passed=False,
                    errors=check.errors,
                    fix_suggestion=check.suggestion
                )
        return ValidationResult(passed=True, warnings=self._check_results(sql))

    def _check_syntax(self, sql: str) -> CheckResult:
        """语法层：用 DuckDB 尝试 EXPLAIN 但不执行"""
        try:
            conn.execute(f"EXPLAIN {sql}")
            return CheckResult(passed=True)
        except Exception as e:
            return CheckResult(
                passed=False,
                errors=[f"语法错误: {str(e)}"]
            )

    def _check_structure(self, sql: str, intent: QueryIntent) -> CheckResult:
        """结构逻辑层：维度匹配 + 条件合理性"""
        errors = []

        # 1. 维度校验：intent 有 dimension → SQL 必须 GROUP BY 该字段
        if intent.dimension and intent.dimension != "null":
            # 从 SQL 中提取 GROUP BY 字段
            group_cols = self._extract_group_columns(sql)
            if intent.dimension not in group_cols:
                errors.append(
                    f"维度不匹配：意图维度为「{intent.dimension}」，"
                    f"但 SQL 按 {group_cols} 分组"
                )

        # 2. 成交口径校验：agg 包含"成交" → SQL 必须有 交易是否达成='是'
        if "成交" in intent.agg.value:
            if "交易是否达成='是'" not in sql:
                errors.append("成交口径缺失：需要交易是否达成='是' 条件")

        # 3. 实体校验：条件中有疾病 → SQL WHERE 应有对应 LIKE
        for cond in intent.conditions:
            ctype, cval = cond["type"], cond["value"]
            if ctype == "disease" and cval not in sql:
                errors.append(f"条件缺失：WHERE 应包含疾病「{cval}」的 LIKE 条件")

        return CheckResult(
            passed=len(errors) == 0,
            errors=errors,
            suggestion=self._generate_fix_suggestion(errors, intent) if errors else None
        )

    def _check_results(self, sql: str, intent: QueryIntent) -> list[str]:
        """结果层（软性）：仅标注警告，不拦截"""
        warnings = []
        # 执行 SQL 检查行数
        try:
            result = conn.execute(sql).fetchall()
            if len(result) == 0:
                warnings.append("⚠️ 结果为空，条件可能过严或实体拼写有误")
            elif len(result) > 10000:
                warnings.append(f"⚠️ 结果较多（{len(result)}行），建议加 LIMIT 或收紧条件")
        except Exception:
            pass  # 语法已在前层校验，这里不重复拦截
        return warnings

    def _generate_fix_suggestion(self, errors: list[str], intent: QueryIntent) -> str:
        """根据校验错误生成自动修复建议"""
        if any("维度不匹配" in e for e in errors):
            return (
                f"建议：GROUP BY 字段改为「{intent.dimension}」，"
                f"并确保 SELECT 中的聚合列匹配意图聚合方式「{intent.agg.value}」"
            )
        if any("成交口径缺失" in e for e in errors):
            return "建议：在 WHERE 子句中添加条件「交易是否达成='是'」"
        return "请检查 SQL 结构与意图是否一致后重试"
```

### 校验流程

```
[SQL 生成]
   │
   ▼
语法层校验 ──失败──→ 提示错误 → 自动重试（同路由，最多1次）
   │ 通过
   ▼
结构逻辑校验 ──失败──→ 提示错误 → 展示建议 → 可以手动编辑后重试
   │ 通过
   ▼
结果层校验（软性）
   │
   ▼
[返回结果 + 警告标注]
```

---

## 八、路由编排 (query_router.py 改造)

### 改造后路由流程

```python
class QueryRouter:
    """查询路由（改造后）"""

    def route(self, question: str) -> QueryResult:
        # Step 1: 意图拆解
        intent = self.intent_translator.translate(question)
        if not intent:
            return self._fallback_llm(question)

        # Step 2: 尝试结构化缓存
        cached = self.cache.get(intent.cache_key)
        if cached:
            return cached

        # Step 3: 尝试模板匹配
        template = self.matcher.match_by_intent(intent)
        if template:
            sql = self.renderer.render(template, intent)
        else:
            # Step 4: LLM 精简翻译（兜底）
            llm_result = self.llm_translator.translate(question, intent=intent)
            if not llm_result.success:
                return llm_result
            sql = llm_result.sql

        # Step 5: SQL 质量门禁
        validation = self.validator.validate(sql, intent)
        if not validation.passed:
            return QueryResult(
                success=False,
                error=f"SQL 校验未通过: {'; '.join(validation.errors)}",
                fix_suggestion=validation.fix_suggestion
            )

        # Step 6: 执行 SQL
        result = self.executor.execute(sql)

        # Step 7: 写入缓存
        self.cache.set(intent.cache_key, sql, result)

        return QueryResult(
            success=True,
            sql=sql,
            data=result,
            warnings=validation.warnings,
            intent=intent,
            template_id=template.id if template else None,
        )
```

---

## 九、前端适配

### 查询结果展示（改造后）

```json
// 后端返回增加字段
{
  "success": true,
  "data": [...],
  "sql": "SELECT ...",
  "warnings": ["⚠️ 结果为空，条件可能过严"],
  "intent": {
    "query_pattern": "distribution",
    "agg": "成交场景数",
    "conditions": [{"type": "disease", "value": "感冒"}],
    "dimension": "城市"
  },
  "route": "template"  // "template" | "llm" | "cache"
}
```

### 前端展示（改造后）

在查询结果区域上方增加意图展示卡片：

```
┌─────────────────────────────────────────────┐
│ 🔍 您查询的是：                               │
│   疾病「感冒」→ 按「城市」→ 「成交场景数」      │
│                                              │
│   条件：感冒                                  │
│   模式：分布排行                              │
│   来源：模板匹配                              │
└─────────────────────────────────────────────┘
```

### 意图修正交互（可选）

如果意图拆解有误，用户可点击修改：
- 修改疾病/药品名
- 修改维度（城市 → 省份）
- 修改聚合方式（场景数 → 成交率）
- 修改后重新查询

---

## 十、实施步骤

### Step 1：新建基础文件
- `intent_schemas.py` — 所有枚举、Schema、意图结构体
- 先不接入路由，单独验证

### Step 2：实现意图拆解器
- `query_intent.py` — LLM 调用 + JSON Schema 校验
- 先写 mock 测试用例，验证对各种问题类型的拆解效果
- 与 `llm_translator.py` 共用一个 API Key 和调用方式

### Step 3：改造模板引擎
- 重新设计 `template_matcher.py`（匹配四元组）
- 新增 `sql_renderer.py`（条件组合 + 维度填充）
- 盘点所有模板，按 YAML 格式整理
- 通过测试用例验证模板渲染正确性

### Step 4：改造 LLM 翻译器
- 精简 `llm_translator.py`，支持接收结构化意图
- 实现模板相似度匹配函数（`get_similar_templates`）
- 保留回退机制（原始全量 Prompt）

### Step 5：新增 SQL 质量门禁
- `sql_validator.py` 三层校验
- 语法层：EXPLAIN 验证
- 结构层：维度 + 口径 + 实体校验

### Step 6：改造缓存
- `query_cache.py` 新 key 格式
- 保留版本机制

### Step 7：改造路由
- `query_router.py` 编排流程

### Step 8：测试环境部署验证
- 部署到 star-query-test（端口 8002）
- 运行回归测试：历史查询是否仍正常工作
- 增量测试：新增查询类型是否能覆盖

### Step 9：同步正式环境
- 自测通过后同步到 star-query（端口 8000）
- 监控缓存命中率、LLM 调用次数变化

---

## 附录一：完整模板清单

### 1. single_stat 单值统计

| ID | agg | 条件 | SQL 骨架 |
|----|-----|------|----------|
| TS01 | 场景数 | 无 | `SELECT COUNT(DISTINCT 场景ID) AS 场景数 FROM data` |
| TS02 | 成交场景数 | deal_yes | `SELECT COUNT(DISTINCT 场景ID) AS 成交场景数 FROM data WHERE 交易是否达成='是'` |
| TS03 | 未成交场景数 | deal_no | `SELECT COUNT(DISTINCT 场景ID) AS 未成交场景数 FROM data WHERE 交易是否达成='否'` |
| TS04 | 成交率 | 无 | `SELECT ROUND(COUNT(DISTINCT CASE WHEN 交易是否达成='是' THEN 场景ID END) * 100.0 / COUNT(DISTINCT 场景ID), 1) AS 成交率 FROM data` |
| TS05 | 问症率 | 无 | `SELECT ROUND(COUNT(DISTINCT CASE WHEN 是否问症='是' THEN 场景ID END) * 100.0 / COUNT(DISTINCT 场景ID), 1) AS 问症率 FROM data` |
| TS06 | 联合用药率 | 无 | `SELECT ROUND(COUNT(DISTINCT CASE WHEN 是否联合用药='是' THEN 场景ID END) * 100.0 / COUNT(DISTINCT 场景ID), 1) AS 联合用药率 FROM data` |
| TS07 | 平均场景时长 | 无 | `SELECT ROUND(AVG(场景时长), 0) AS 平均场景时长 FROM data` |
| TS08 | 场景时长中位数 | 无 | `SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY 场景时长) AS 场景时长中位数 FROM data` |

### 2. distribution 分布

#### 2.1 无条件分布

| ID | 维度 | agg | SQL 骨架 |
|----|------|-----|----------|
| TD01 | 省份 | 场景数 | `SELECT 省份, COUNT(DISTINCT 场景ID) AS 场景数 FROM data GROUP BY 省份 ORDER BY 场景数 DESC LIMIT {limit}` |
| TD02 | 城市 | 场景数 | `SELECT 城市, COUNT(DISTINCT 场景ID) AS 场景数 FROM data GROUP BY 城市 ORDER BY 场景数 DESC LIMIT {limit}` |
| TD03 | 疾病名称 | 场景数 | `SELECT 疾病名称, COUNT(DISTINCT 场景ID) AS 场景数 FROM data GROUP BY 疾病名称 ORDER BY 场景数 DESC LIMIT {limit}` |
| TD04 | 门店 | 场景数 | `SELECT 门店, COUNT(DISTINCT 场景ID) AS 场景数 FROM data GROUP BY 门店 ORDER BY 场景数 DESC LIMIT {limit}` |
| TD05 | 连锁 | 场景数 | `SELECT 连锁, COUNT(DISTINCT 场景ID) AS 场景数 FROM data GROUP BY 连锁 ORDER BY 场景数 DESC LIMIT {limit}` |
| TD06 | 场景时长档 | 场景数 | `SELECT CASE WHEN 场景时长<60 THEN '1分钟内' ... END AS 时长档, COUNT(DISTINCT 场景ID) AS 场景数 FROM data GROUP BY 时长档 ORDER BY 时长档` |
| TD07 | 顾客性别 | 场景数 | `SELECT 顾客性别, COUNT(DISTINCT 场景ID) AS 场景数 FROM data GROUP BY 顾客性别 ORDER BY 场景数 DESC` |
| TD08 | 月度 | 场景数 | `SELECT SUBSTR(ydate,1,7) AS 月份, COUNT(DISTINCT 场景ID) AS 场景数 FROM data GROUP BY 月份 ORDER BY 月份` |
| TD09 | 省份 | 成交场景数 | `SELECT 省份, COUNT(DISTINCT 场景ID) AS 成交场景数 FROM data WHERE 交易是否达成='是' GROUP BY 省份 ORDER BY 成交场景数 DESC` |
| TD10 | 城市 | 成交场景数 | `SELECT 城市, COUNT(DISTINCT 场景ID) AS 成交场景数 FROM data WHERE 交易是否达成='是' GROUP BY 城市 ORDER BY 成交场景数 DESC` |
| TD11 | 省份 | 成交率 | `SELECT 省份, ROUND(COUNT(DISTINCT CASE WHEN 交易是否达成='是' THEN 场景ID END) * 100.0 / COUNT(DISTINCT 场景ID), 1) AS 成交率 FROM data GROUP BY 省份 ORDER BY 成交率 DESC` |
| TD12 | 城市 | 成交率 | `SELECT 城市, ROUND(COUNT(DISTINCT CASE WHEN 交易是否达成='是' THEN 场景ID END) * 100.0 / COUNT(DISTINCT 场景ID), 1) AS 成交率 FROM data GROUP BY 城市 ORDER BY 成交率 DESC` |
| TD13 | 疾病名称 | 成交场景数 | `SELECT 疾病名称, COUNT(DISTINCT 场景ID) AS 成交场景数 FROM data WHERE 交易是否达成='是' GROUP BY 疾病名称 ORDER BY 成交场景数 DESC` |
| TD14 | 疾病名称 | 成交率 | `SELECT 疾病名称, ROUND(...成交率...), 1) AS 成交率 FROM data GROUP BY 疾病名称 ORDER BY 成交率 DESC` |

#### 2.2 带条件分布（通用模板）

通用模板通过意图的 conditions 字段渲染 WHERE 子句，仅列出代表性 ID：

| ID | 模式 | 说明 |
|----|------|------|
| TX01 | distribution + disease | 某疾病按某维度分布 |
| TX02 | distribution + drug_any | 某药品按某维度分布 |
| TX03 | distribution + disease + deal_yes | 某疾病成交按维度分布 |
| TX04 | distribution + drug_any + deal_yes | 某药品成交按维度分布 |

通用 SQL 骨架：
```sql
SELECT {dimension}, {agg_formula}
FROM data
{conditions_where}
GROUP BY {dimension}
ORDER BY {agg_order}
LIMIT {limit}
```

### 3. trend 趋势

| ID | 时间粒度 | agg | SQL 骨架 |
|----|---------|-----|----------|
| TT01 | 月度 | 场景数 | `SELECT SUBSTR(ydate,1,7) AS 月份, COUNT(DISTINCT 场景ID) AS 场景数 FROM data {conditions} GROUP BY 月份 ORDER BY 月份` |
| TT02 | 月度 | 成交场景数 | `SELECT SUBSTR(ydate,1,7) AS 月份, COUNT(DISTINCT 场景ID) AS 成交场景数 FROM data WHERE 交易是否达成='是' {conditions} GROUP BY 月份 ORDER BY 月份` |
| TT03 | 月度 | 成交率 | `SELECT SUBSTR(ydate,1,7) AS 月份, ROUND(COUNT(DISTINCT CASE WHEN 交易是否达成='是' THEN 场景ID END) * 100.0 / COUNT(DISTINCT 场景ID), 1) AS 成交率 FROM data {conditions} GROUP BY 月份 ORDER BY 月份` |

### 4. comparison 对比

| ID | 对比内容 | SQL 骨架 |
|----|---------|----------|
| TC01 | 两种疾病场景数对比 | `SELECT '{d1}' AS 疾病, COUNT(DISTINCT 场景ID) AS 场景数 FROM data WHERE 疾病名称 LIKE '%{d1}%' UNION ALL SELECT '{d2}' AS 疾病, COUNT(DISTINCT 场景ID) AS 场景数 FROM data WHERE 疾病名称 LIKE '%{d2}%'` |
| TC02 | 两种药品成交率对比 | `SELECT '{d1}' AS 药品, ROUND(COUNT(DISTINCT CASE WHEN 交易是否达成='是' THEN 场景ID END) * 100.0 / COUNT(DISTINCT 场景ID), 1) AS 成交率 FROM data WHERE CONTAINS(..., '{d1}') UNION ALL ...` |

### 5. detail 明细

| ID | 说明 | SQL 骨架 |
|----|------|----------|
| DL01 | 场景明细 | `SELECT * FROM data {conditions} LIMIT {limit}` |

### 6. ratio 占比

| ID | 说明 | SQL 骨架 |
|----|------|----------|
| RT01 | 疾病/药品占总场景比例 | `SELECT ROUND(COUNT(DISTINCT 场景ID) * 100.0 / (SELECT COUNT(DISTINCT 场景ID) FROM data), 1) AS 占比 FROM data {conditions}` |

---

## 附录二：与现有系统兼容性

### API 兼容性

现有 API 接口 `POST /api/translate` 的输入输出格式**不做破坏性变更**，仅扩展：

```python
# 输入（不变，原样接收 question 字符串）
{"question": "感冒各城市成交场景数"}

# 输出（扩展，不影响前端现有解析）
{
    "success": True,
    "data": [...],           # 不变
    "sql": "SELECT ...",     # 不变
    "elapsed_ms": 234,       # 不变
    # 以下为新扩展字段：
    "intent": {...},         # 结构化意图
    "route": "template",     # 路由来源
    "warnings": [...]        # 结果层警告
}
```

### 回退保证

1. **意图拆解器失败** → 回退原始全量 Prompt LLM 翻译
2. **模板渲染异常** → 回退精简 LLM 翻译或原始全量 Prompt
3. **缓存异常** → 跳过缓存，直接走后续路径
4. **质量门禁异常** → 跳过校验，直接执行 SQL

每个步骤都有 try-catch → 降级 → 核心功能不中断。

---

## 附录三：依赖清单

| 新增依赖 | 用途 | 是否必须 |
|---------|------|---------|
| `pydantic`（如已存在则复用） | QueryIntent 的 Schema 校验 | ✅ 强烈建议 |
| 无其他新增 | 所有功能基于现有依赖实现 | — |

---

## 附录四：效果预估

| 指标 | 改造前 | 改造后 |
|------|--------|--------|
| 平均 Prompt 大小 | ~3000 tokens | 意图拆解 ~200t + SQL 生成 ~500t |
| 每次查询 LLM 调用次数 | 1 次 | 模板命中 1 次；未命中 2 次（意图 + 精简） |
| 模板命中率预估 | — | 常见查询 ~60% (分析：无 LLM 调用) |
| LLM 调用减少 | — | 模板命中时 +100%；未命中时多 1 次短调用 |
| 维度错配 BUG | 高频 | 结构调整后基本消除 |
| 缓存命中率 | 低（字符串级） | 高（语义级结构化 key） |
| 新增查询类型成本 | 写 Few-shot 示例 | 写模板或调整意图映射规则 |
