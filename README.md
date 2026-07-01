# 星宝语料场景查询系统 v3.6.0（2026-06-30）

自然语言 → SQL 的医药零售数据查询系统。
技术栈：FastAPI + React + DuckDB + DeepSeek API。
数据量：**2,153,796 行**。数据来源：阿里云 ClickHouse（每天 6:00 增量同步 + ATC 映射 enrich）。

## 核心架构：7 层防御 Pipeline + 去重计数扩展

```
用户问题 → ①意图拆解 → ②结构化缓存 → ③模板匹配(四元组) → ④LLM精简(兜底) → ⑤SQL质量门禁 → 执行 → ⑥结果交叉验证
                                                                                                  ↓
                                                                                         ⑦反馈闭环(incident)
```

| 层 | 方案 | 说明 |
|----|------|------|
| ① | 意图拆解 | DeepSeek 将自然语言拆为 `(pattern, agg, conditions, dimension)` 四元组 |
| ② | 结构化缓存 | 基于四元组 md5 的 key，同语义不同表述可命中 |
| ③ | 模板匹配 | ~30 个预置模板（16 个自包含常用查询+14 个意图匹配用），四元组精确+通配符双模式，drug_* 归一化 |
| ④ | LLM 精简翻译 | 仅注入当前查询所需字段(~500 tokens)，带药品上下文+字段映射 |
| ⑤ | 质量门禁 | 维度校验+成交口径校验+条件实体校验+合理性校验，自动恢复 |
| ⑥ | 结果交叉验证 | 锚点查询比对，维度之和 vs 全量偏差>5%报警 |
| ⑦ | 反馈闭环 | 校验失败/用户踩→incident→AI分析根因→出方案 |

### 去重计数扩展（三闸门架构）

当用户查询「去重店员ID按省份」时，系统通过三层拦截正确识别动态字段：

```
① 意图校验层：agg='去重店员ID数量' 非法 → 匹配「去重+字段」模式 → 提取 dedup_field='店员ID'
② 意图结构层：QueryIntent.dedup_field 携带字段名 → cache_key 含 dedup_field 防止不同字段互相命中
③ SQL 渲染层：检测 dedup_field → 将 COUNT(DISTINCT 场景ID) 替换为 COUNT(DISTINCT 店员ID)
```

支持字段：店员ID、门店ID、药师ID、顾客ID、场景ID、疾病名称

## 快速开始

```bash
# 启动（测试环境）
cd star-query/backend && bash safe-restart.sh --test

# 启动（正式环境）
bash safe-restart.sh --prod

# 重启+回归测试
bash safe-restart.sh --prod --regtest
```

## 环境

| 环境 | 地址 | 端口 | 数据 |
|------|------|------|------|
| 正式 | http://49.232.90.75:8000 | 8000 | 2,153,796 行 |
| 测试 | http://49.232.90.75:8002 | 8002 | 2,153,796 行 |

## 后端文件

| 文件 | 职责 |
|------|------|
| `app.py` | FastAPI 入口 |
| `intent_schemas.py` | 结构化意图枚举+Schema |
| `query_intent.py` | 意图拆解器（LLM 调用） |
| `template_matcher.py` | 预置模板匹配器（四元组） |
| `sql_renderer.py` | SQL 渲染引擎 |
| `llm_translator.py` | LLM 自然语言→SQL 翻译（全量+精简双路径） |
| `query_router.py` | 查询路由（v3 Pipeline 编排） |
| `sql_engine.py` | DuckDB 查询引擎 |
| `sql_validator.py` | SQL 质量门禁（维度/口径/实体/合理性） |
| `query_cache.py` | 查询缓存（三层+结构化+信任等级） |
| `cross_validator.py` | 结果交叉验证 |
| `incident_writer.py` | 反馈事件写入 |
| `incident_analyzer.py` | 反馈事件分析 |
| `regression_test.py` | 回归测试 |
