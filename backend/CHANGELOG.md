
## v3.1.1 — 2026-05-26 (P0 条件类型归一化)

### 修复
- **P0 条件类型归一化**：`template_matcher.py` 新增 `_normalize_cond_types()` 方法，在模板匹配层将 `drug_mentioned/drug_named/drug_ordered` 归一化为 `drug_any` 家族。
  - 根因：意图拆解可能输出 `drug_mentioned`，但模板要求 `drug_any`，精确匹配失败→走 LLM fallback→可能生成错误 SQL
  - 修复范围：`match_by_intent()` 和 `get_similar_templates()` 均应用归一化
  - 不影响的：SQL 渲染（仍使用原始精确类型）、缓存 key（使用原始类型）、前端展示
  - 已验证：所有 `drug_*` → `drug_any` 匹配 + 对应字段精确过滤 SQL 正确生成

## v3.1.2 — 2026-05-29 (常用查询修正 + SQL渲染器BUG修复)

### 修复
- **P0 SQL渲染器 UNNEST 别名不一致**：`sql_renderer.py` 中 `DRUG_UNNEST_MAP` 使用 `s(item)` 作别名，但 `_render_dimension()` 返回 `t.drug`，导致药品维度模板生成的 SQL 列名不匹配，查询执行失败。
  - 修复：统一 UNNEST 别名为 `t(drug)`，与渲染器一致
  - 影响模板：tx02（药品分布）、tx04（药品成交分布）、tx06-tx09（药品-城市/省份分布）、tx10（已删除）
- **P1 常用查询按钮文案与实际查询不匹配**：`template_matcher.py` 中 tx01「疾病分布」的 `question` 为"疾病按城市分布"（需疾病参数），tx02「药品分布」为"药品按省份分布"（需药品参数），用户点击后返回错误结果。
  - 修复：tx01 `question` 改为"各疾病分布"，`intent_key` 改为无条件疾病排名
  - 修复：tx02 `question` 改为"各药品分布"，`intent_key` 改为无条件药品排名
  - 修复：tx04 `question` 改为"药品成交分布"，`intent_key` 改为无条件药品成交排名
- **P1 常用查询按钮包含需交互参数的模板**：tx03/tx05/tt01/tt02/tx06-tx09 共8个模板需用户指定疾病/药品参数，但前端无交互控件，点击后文不对题。
  - 修复：`get_all_templates()` 过滤掉 `condition_types` 非空的模板，仅返回自包含查询
  - 常用查询从24个精简至16个

### 优化
- **sql_validator.py** — LIMIT 截断豁免：当 `LIMIT N` 且 `row_count >= N` 时跳过"结果较多"告警
- **sql_validator.py** — single_stat 豁免：当 `query_pattern=single_stat` 时不要求 GROUP BY
- **query_intent.py** — 合法 Dimension 枚举值保留，不被条件清洗误删

### 变更
- 删除重复模板 tx10「产品分布」（与 tx02「药品分布」逻辑相同）
- 保留 tx03/tx05/tt01/tt02/tx06-tx09 在模板列表中，仅从前端按钮隐藏（仍用于意图匹配路由）
