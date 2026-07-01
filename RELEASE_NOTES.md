# 星宝语料场景查询系统 — 版本升级说明

---

## v3.6.0（2026-06-30）

### 药品 ATC 映射表集成 + 全链路 enrich + 增量管道

**改了什么：**
将 Bayer Report 项目沉淀的药品标准化映射引擎（ATC/TCM v13）集成到星宝数据平台，实现入库数据自动标准化 enrich。

#### 1. 药品标准化映射引擎

- 映射表：**220,085 条**药品名标准化，**73.8%** 匹配率
- ATC 字典：8,153 条（合并版），SKU 品牌映射 66,293 条
- 标准分类：4,409 条 step2 分类
- 查询/导出全路径自动 ATC enrich（SQL 查询 + 自然语言 + 分页 + 回退）

#### 2. 导出增强

- CSV / Parquet 导出自动 JOIN **10 个 ATC 标准化字段**
- 下载文件自带 `drug_mapping` 分类信息

#### 3. 增量管道

- `clickhouse-daily-sync.sh` 每日 6:00 增量同步后自动触发映射 enrich
- `reload-mapping` API 支持映射表热加载（无需重启服务）

#### 4. Bug 修复

- `chart_builder.py` KeyError 修复
- 导出面板疾病筛选两级联动恢复

#### 5. 性能优化

- `filter-options` 接口缓存化：**4.3s → 3ms**

---

## v3.5.0（2026-06-11）

### 用户管理规范化 + Schema 知识全量更新

**改了什么：**
将11个测试账号从流水号重命名为便于识别的名字，同时基于ClickHouse数据全量更新后的48个字段，更新了Schema知识和用户字典。

#### 1. 用户批量重命名

| 原用户名 | → 新用户名 | 密码 |
|---------|-----------|------|
| sales1 | ella | ella |
| sales2 | hubo | hubo |
| sales3 | liumd | liumd |
| sales4 | dongjl | dongjl |
| user1 | amy | amy |
| user2 | wim | wim |
| user3 | chenml | chenml |
| user4 | niuhr | niuhr |
| user5 | huangmy | huangmy |
| user6 | linyq | linyq |
| user7 | gesl | gesl |

- 保留所有历史登录记录和查询记录（login_log 42条 + query_history 369条）
- user8 保持不变

#### 2. Schema 知识全量更新（48字段全面覆盖）

**字段总数：33 → 48（+15个）**

| 新增字段 | 类型 | 说明 |
|---------|------|------|
| 会话ID | BIGINT | 场景所属的原始会话编号 |
| 会话开始时间 | VARCHAR | 会话的开始时间 |
| 场景从会话的开始时间 | VARCHAR | 场景在会话内的偏移时间 |
| 场景日期 | VARCHAR | 场景发生的日期 |
| 店员提及药品JSON | VARCHAR | 药师说出的药品名 |
| 店员推荐药品JSON | VARCHAR | 药师主动推荐的药品 |
| 用药人年龄分层 | VARCHAR | 年龄分层 |
| 联合用药动作 | BIGINT | 联合推荐行为标识 |
| 推荐的联合用药JSON | VARCHAR | 联合用药具体方案 |
| 综合置信度评分 | DOUBLE | 数据整体置信度 |
| 场景完整度 | VARCHAR | 数据完整度 |
| 业务置信度 | VARCHAR | 业务可用性评级 |
| 是否商用 | BIGINT | 商用标准标记 |
| 切割置信度分值 | DOUBLE | 场景切割准确度 |
| 切割完整度分值 | DOUBLE | 场景切割完整度 |

**类型修正（5个）：** `联合用药动作` VARCHAR→BIGINT, `综合置信度评分` VARCHAR→DOUBLE, `是否商用` VARCHAR→BIGINT, `切割置信度分值` VARCHAR→DOUBLE, `切割完整度分值` VARCHAR→DOUBLE

**业务规则和查询技巧同步更新：**
- 联合用药分析优先使用`联合用药动作`字段
- `店员推荐药品JSON`与`顾客点名药品`对比可分析药师影响力
- `是否商用=1`过滤高质量数据
- `综合置信度评分`评估数据可靠性

#### 3. 用户字典升级（40→59条，全部带示例）

| 维度 | 原数量 | 新数量 | 示例率 |
|------|-------|-------|--------|
| 业务指标 | 7 | 7 | 100% |
| 字段释义 | 28 | 42 | 100% |
| 常见概念 | 5 | 10 | 100% |
| **总计** | **40** | **59** | **100%** |

新增词条覆盖：会话相关(4)、店员相关(3)、置信度体系(5)、场景切割(2)、年龄分层(1)等。

#### 变更清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `backend/schema_knowledge.py` | **改造** | 字段数33→48，新增4字段，修正5类型，更新业务规则和查询技巧 |
| `backend/schema_ddl.py` | **改造** | FIELD_COMMENTS从12个扩至18个，DDL全量刷新 |
| `frontend/src/data/dictionary.ts` | **改造** | 字典40→59条，全部补充示例 |
| `backend/app.py` | **更新** | version 3.2.0 → 3.5.0 |
| `.env` | **更新** | USERS 全部刷新为新用户名 |

#### 部署说明
- 用户重命名一次性脚本执行，不新增功能
- 测试环境（8002）开发验证完成
- 正式环境重启：`safe-restart.sh --prod`，PID 76228

### Text-to-SQL Prompt 系统性改造

**改了什么：**
修复 LLM 翻译层丢失时间条件（如"最近7天"→ SQL 无 ydate 过滤）的系统性根因，
参考 DAIL-SQL、SQLCoder 等业界最佳实践，对 Prompt 架构进行全面重构。

#### 5 步改造

| Step | 文件 | 核心改造 |
|------|------|---------|
| 1 | `schema_ddl.py` 🆕 | DDL 自动生成模块（参考 DAIL-SQL CR 表示法），替代自然语言字段列表 |
| 2 | `query_intent.py` | 移除 13 种硬编码条件类型，改为 DDL 驱动推理；新增 time_range 后处理（正则→标准化）+ 自动兜底 |
| 3 | `sql_renderer.py` | 新增 `_render_time_range_condition()`，支持 9 种时间范围→ DuckDB 日期SQL |
| 4 | `llm_translator.py` | Schema 从自然语言→DDL；18 条硬编码示例→8 类分类示例库动态选 2-3 条；Prompt 从~3000 chars→~1700 chars（↓42%） |
| 5 | `sql_validator.py` + `query_router.py` | 新增 `validate_time_consistency()` 检测遗漏时间条件，三路径接入（主查询/回退/SQL直查） |

#### 时间范围支持一览

| 用户说 | SQL 生成 |
|--------|---------|
| 最近N天 / 近N天 | `ydate >= CURRENT_DATE - INTERVAL 'N' DAY` |
| 最近N周 | `ydate >= CURRENT_DATE - INTERVAL 'N*7' DAY` |
| 最近N个月 | `ydate >= CURRENT_DATE - INTERVAL 'N' MONTH` |
| 最近N年 | `strftime(ydate, '%Y') >= CAST(... AS INTEGER) - N` |
| 本月 | `strftime(ydate, '%Y-%m') = strftime(CURRENT_DATE, '%Y-%m')` |
| 本周 | `ydate >= DATE_TRUNC('week', CURRENT_DATE) ...` |
| 今年 | `strftime(ydate, '%Y') = strftime(CURRENT_DATE, '%Y')` |
| 昨天/今天 | `ydate = CURRENT_DATE +/- INTERVAL '1' DAY` |

#### 变更清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `backend/schema_ddl.py` | **新增** | DDL 自动生成，完整版 1706 chars / 精简版 1007 chars |
| `backend/query_intent.py` | **改造** | DDL 驱动意图拆解 + time_range 后处理 + 自动兜底 |
| `backend/sql_renderer.py` | **改造** | CONDITION_SQL_MAP 新增 time_range，动态渲染 9 种时间映射 |
| `backend/llm_translator.py` | **改造** | DDL 替代自然语言，分类示例库 8 类 20 条，动态选取，Prompt 减 42% |
| `backend/sql_validator.py` | **改造** | 新增 `validate_time_consistency()` 时间一致性校验 |
| `backend/query_router.py` | **改造** | 三路径接入时间校验（query / _query_fallback / query_sql）|

#### 部署说明
- 测试环境（star-query-test）开发验证完成
- 正式环境同步：`safe-restart.sh --prod`
- 备份：`backups/lightclaw-backup-20260601_032126-prompt改造前备份.tar.gz`（89MB）
- 改造后快照：`backups/2026-06-01-prompt-reform-v1/`（6 个改造文件）

### ClickHouse 数据源自动同步

**改了什么：**
数据源从手动上传 xlsx 改为从阿里云 ClickHouse 自动拉取。

#### 变更清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `clickhouse-sync.py` | 新增 | ClickHouse 数据同步脚本（`--mode full` 全量 / `--mode daily` 增量） |
| `clickhouse-daily-sync.sh` | 新增 | crontab 每日增量脚本（每天 6:00 执行） |
| `insights.py` | 修复 | `ydate` 字段类型转换：`pd.Timestamp` → `strftime('%Y-%m-%d')`，修复趋势和日期范围显示 |
| `.env` | 更新 | `DATA_PATH=/root/All_data_ch_full.parquet` |
| `backend/schema_knowledge.py` | 更新 | total_rows=1,374,589 |

#### 运维要点
- 数据源：阿里云 ClickHouse `yaoxin_ai` 库（只读账号）
- 同步时间：每天 6:00（同事 5:20 清洗前一日数据）
- 数据量：1,374,589 行，200 MB
- 回滚：切回 `DATA_PATH=/root/All_data20260514.parquet`

---

## v3.1.0 → v3.2.0（2026-05-26）

### 明细分页查询 + CSV 全部数据导出

**改了什么：**
新增明细分页查询和 CSV 全量导出功能，解决大结果集浏览和数据导出场景。

#### 变更清单

| 文件 | 类型 | 说明 |
|------|------|------|
| **改造** `query_router.py` | 新增 | `_execute_paginated()` 分页执行函数：COUNT+子查询包装+ORDER BY 场景ID |
| **改造** `query_router.py` | 新增 | `POST /api/query/export` 路由，DuckDB COPY 导出全部数据为 CSV |
| **改造** `models.py` | 改造 | `QueryRequest` 新增 `page`/`page_size` 字段；`QueryResult` 新增 `pagination` 字段；新增 `ExportRequest` 模型 |
| **改造** `sql_engine.py` | 改造 | 新增 `conn` property 暴露 DuckDB 连接，用于 CSV 导出 |
| **改造** `QueryPage.tsx` | 改造 | 分页导航组件（总数+翻页）+ 服务端 CSV 下载切换 |
| **改造** `App.css` | 新增 | 分页导航 CSS |

#### 修复

| 问题 | 修复 |
|------|------|
| 明细分页摘要显示 `TOP1: 五官疾病-牙疼 = nan` | 有 pagination 时直接显示共 N 条结果 |
| CSV 导出报错 `ExportRequest has no field page` | 改为 QueryRequest 传给 query() |
| CSV 导出报错 `ENCODING not supported for writing` | 去掉 DuckDB COPY TO 的 ENCODING 参数 |
| CSV 下载后 Windows Excel 打开乱码 | 写入后 prepend UTF-8 BOM |

#### 部署说明
- 测试环境（8002）安全重启验证通过
- 正式环境（8000）使用 `safe-restart.sh` 部署，PID 1922740

### 重大架构变更：Prompt 拆解 Pipeline

**改了什么：**
将一步翻译（用户问题 → LLM → SQL）拆分为多步结构化 Pipeline：
```
用户问题 → 意图拆解 → 结构化缓存 → 模板匹配(四元组) → LLM精简(兜底) → SQL质量门禁 → 执行
```

#### 变更清单

| 文件 | 类型 | 说明 |
|------|------|------|
| **新增** `intent_schemas.py` | +182行 | 结构化意图所有枚举、Schema、条件类型定义（QueryPattern/Aggregation/ConditionType/Dimension） |
| **新增** `query_intent.py` | +280行 | 意图拆解器，DeepSeek 独立调用 + JSON Schema 严格校验 + 泛指词后过滤 |
| **新增** `sql_renderer.py` | +170行 | SQL 渲染引擎，条件组合 + 维度映射 + `{conditions}`/`{conditions_extra}` 双占位符 |
| **改造** `template_matcher.py` | 10→40+模板 | 从正则匹配 → 四元组 `(pattern, agg, cond_types, dim)` 意图匹配，细粒度+通配符兜底 |
| **改造** `sql_validator.py` | 新增 | 三层校验：语法层(EXPLAIN) → 结构层(维度/口径/实体) → 结果层(软性标注) |
| **改造** `query_router.py` | 重写 | v3 Pipeline 编排：意图拆解 → 缓存 → 模板 → LLM → 校验 → 执行，v2 旧路径保留为 fallback |
| **改造** `llm_translator.py` | Prompt 精简 | 从全量注入(~3000 tokens)改为结构化注入(~500 tokens)，仅注入当前查询所需字段+1-2相似示例 |
| **改造** `query_cache.py` | 缓存 key 重构 | 从 md5(问题字符串) → md5(结构化JSON)，同语义不同表述可命中 |

#### 部署说明
- 测试环境验证部署：2026-05-25，安全重启后 4 个测试用例全部通过
- 正式环境部署：2026-05-25，使用 `safe-restart.sh --prod`（PID 1570239，全量 953,762 行）
- 备份：`/root/backups/star-query-v21-20260525_143050/`（v2.1，回滚用）
- 技术方案文档：`docs/003-prompt-decomposition-tech-design.md`
- 变更说明文档：`docs/20260525-变更说明.md`

---

## v2.0.0（2026-05-15）

### 重大架构变更：LLM 理解语义 + 三层缓存

**改了什么：**
移除「正则模板猜语义」的 L1 模糊模板（疾病/城市/药品查询），改为 LLM 理解自然语言后翻译 SQL，配合三层缓存加速重复查询。

#### 旧架构（v1.x）
```
用户 → 正则模板 → 猜语义 → 猜错了就加补丁 → 无限循环
                        ↓
                   "感冒被当药品"  → 加疾病库
                   "广州被当药品"  → 加城市白名单
                   "下一个漏在哪"  → 不知道
```

#### 新架构（v2.0）
```
用户 → [精确模板] → 命中 → 毫秒级返回
     → [三层缓存] → 命中 → 毫秒级返回
     → [LLM 翻译]  → 写入缓存 → 正确SQL
```

### 变更清单

| 文件 | 变更 | 说明 |
|------|------|------|
| **新增** `query_cache.py` | +185行 | 三层缓存引擎（精确/归一化/语义） |
| **新增** `domain_knowledge.py` | +200行 | 领域知识引擎（药品库28k/疾病180/城市157） |
| **新增** `domain_data.json` | 265KB | 从 drug-analysis 映射表提取的知识数据 |
| **修改** `template_matcher.py` | 简化至63行 | 仅保留10个确定性精确模板 |
| **修改** `query_router.py` | +12行 | 集成三层缓存流程 |
| **修改** `models.py` | +1行 | QueryInfo.source 支持 "cache" 值 |
| **删除** | t_disease/t_city/t9 模板 | 不再用正则猜疾病/城市/药品 |

### 保留的精确模板（确定性业务语义）

| ID | 查询 | 匹配条件 |
|----|------|---------|
| t1 | 总场景数 | 精确匹配"总场景数" |
| t2 | 疾病TOP10 | "疾病 TOP10/排名/分布" |
| t3 | 城市分布 | "城市 TOP20/排名/分布" |
| t4 | 月度趋势 | "月度/月份趋势/分布" |
| t5 | 问症率 | "问症率/问症统计" |
| t6 | 联合用药率 | "联合用药率/联合用药统计" |
| t7 | 省份分布 | "省份/省分布/排名" |
| t8 | 场景时长分布 | "场景时长/时长分布" |
| t10 | 成交率 | "成交率/成交统计" |
| t11 | 关键信息到达率 | "关键信息到达率" |

### 三层缓存说明

| 层 | 匹配方式 | 命中速度 | 示例 |
|----|---------|---------|------|
| L1 | 原始问题完全一致 | <1ms | "感冒场景数" → SQL |
| L2 | 去停用词后归一化 | <1ms | "感冒场景数" ≈ "感冒" |
| L3 | 语义类型+关键词 | <1ms | disease:感冒 → 同类问题命中 |

### 升级影响

| 场景 | 首次查询 | 重复查询 |
|------|---------|---------|
| 精确模板匹配 | 不变（8ms） | 不变 |
| 疾病/城市/药品查询 | **变慢**（8ms→3-8s，因走LLM） | **变快**（8ms→<1ms，因缓存命中） |
| 复杂模糊查询 | 不变（依然走LLM） | **变快**（新增缓存） |
| 查询准确性 | **大幅提升**（LLM真正理解语义） | 不变 |

---

## v2.1.0（2026-05-21）

### 查询质量优化：缓存下毒修复 + SQL交叉验证 + 反馈闭环

**改了什么：**
基于476条历史全量查询分析（正确率82.1%），修复了两个主导错误模式（自相矛盾WHERE条件49次、明细→GROUP BY错配16次），共占全部问题SQL的98%。

#### 变更清单

| 文件 | 变更 | 说明 |
|------|------|------|
| **修改** `query_cache.py` | 重写 `extract_semantic_key()` | 语义key从`disease:X`升级为`entity:val\|agg:X\|dim:Y`三维度组合，根治缓存下毒 |
| **修改** `query_cache.py` | 新增 `invalidate_by_question()` | 支持按问题文本精确+语义双路径清除缓存（被踩/编辑SQL时调用） |
| **新增** `query_cache.py` | `_extract_entity()` / `_extract_aggregation()` / `_extract_dimension()` | 规则引擎提取查询意图维度，不依赖LLM |
| **新增** `query_cache.py` | 噪声药名过滤 | 过滤"感冒的""5块钱的药"等口语化噪声，不影响药品识别 |
| **修改** `query_router.py` | 新增 `_validate_sql_structure()` | SQL意图-结构交叉验证：明细→聚合错配自动改写，维度不匹配告警 |
| **修改** `query_router.py` | `POST /api/query/sql` 支持 `original_question` | 用户编辑SQL后自动同步缓存 |
| **修改** `query_router.py` | `POST /api/feedback` 踩→清缓存 | 反馈数据不再只存不用 |
| **修改** `models.py` | `SqlQueryRequest` 新增 `original_question` 字段 | 前端编辑SQL时传参触发缓存同步 |
| **修改** `llm_translator.py` | 药品名采样过滤噪声 | 不再截取 sorted[:30] 的噪声数据 |
| **修改** `llm_translator.py` | 新增3个Few-shot示例 | "帮我查一下"复杂句式 + "明细" SELECT * 示例 |

#### 语义key粒化对比

| 用户问题 | v2.0 旧key（有毒） | v2.1 新key（安全） |
|---------|-----------------|-----------------|
| 高血压的场景数 | `disease:高血压` | `disease:高血压\|agg:total` |
| 高血压的场景数，按省份呈现 | `disease:高血压` | `disease:高血压\|agg:distribution\|dim:province` |
| 高血压的城市分布 | `disease:高血压` | `disease:高血压\|agg:distribution\|dim:city` |
| 诺欣妥场景明细 | `drug:诺欣妥` | `drug:诺欣妥\|agg:detail` |
| 三九感冒灵成交场景 | `disease:感冒` ❌ | `drug:三九感冒灵\|agg:total` ✅ |
| 帮我查一下有多少三九感冒灵的成交场景 | 药品名无法提取 ❌ | `drug:三九感冒灵\|agg:total` ✅ |

#### 部署说明
- 测试环境验证通过：2026-05-21 15:57
- 正式环境部署：2026-05-21，使用 `safe-restart.sh --prod`

### 新增功能

| 功能 | 说明 |
|------|------|
| **SQL 编辑器** | 在「查看算法」面板中将只读 SQL 改为可编辑 textarea，用户改完可「修改并运行」 |
| **修改密码** | Header 新增 🔑 按钮，用户可自行修改登录密码（SHA-256 哈希存储） |
| **安全重启脚本** | `safe-restart.sh`，一键安全重启（优雅关闭→端口等待→健康检查轮询） |

### 变更清单

| 文件 | 变更 |
|------|------|
| `query_router.py` | 无变动（/api/query/sql 接口已存在） |
| `auth.py` | bcrypt → SHA-256 哈希（绕开版本兼容问题） |
| `auth_router.py` | 新增 POST /api/auth/change-password 端点 |
| `password_store.py` | 新增，SQLite 表 password_override |
| `models.py` | 新增 ChangePasswordRequest/Response |
| `QueryPage.tsx` | SQL 编辑器 + 🔑 修改密码入口 |
| `ChangePasswordModal.tsx` | 新增，修改密码弹窗组件 |
| `App.css` | SQL 编辑器样式 + 通用 Modal 样式 |
| `safe-restart.sh` | 新增，安全重启脚本 |

---

## v1.2.0（此前版本）

### 已有功能

| 功能 | 说明 |
|------|------|
| JWT 认证 | 多用户登录，Token 鉴权 |
| L1 模板匹配 + L2 LLM 翻译 | 自然语言→SQL 翻译 |
| 数据看板 | 今日/本周场景数、成交率、问症率、联合用药率、趋势图、疾病TOP5 |
| 历史记录 | SQLite 存储、分页、关键词搜索、按用户隔离 |
| 反馈机制 | 赞/踩，同一查询可覆盖 |
| 登录日志 | 查看各用户登录记录 |
| 字段字典 | 字段含义查询 Modal |
| 大字体切换 | 一键切换大号字体 |
| 移动端适配 | @media 响应式 + iOS 滑出抽屉 |
| ECharts 图表 | 柱状图/饼图/折线图/分组柱状图 |
| Parquet 数据加速 | DuckDB 引擎，一键更新脚本 |

---

## v3.0.0 → v3.1.0（2026-05-26）

### 7 层防御体系全线部署

**改了什么：**
从"能查询"到"查得准"，围绕 SQL 质量新增 7 层防御体系：

#### 变更清单

| 文件 | 类型 | 说明 |
|------|------|------|
| **改造** `template_matcher.py` | P0-① | 条件类型归一化：drug_mentioned/drug_named/drug_ordered → drug_any 匹配 |
| **改造** `query_router.py` | P0-② | 质量门禁自动恢复：校验失败→自动重试→缓存降级→重新生成 |
| **改造** `llm_translator.py` | P1-① | 精简 Prompt 补条件映射+药品上下文，涉及药品时动态注入 |
| **改造** `query_cache.py` | P1-② | 三级缓存信任等级：ephemeral(LLM生成)/confirmed(模板)/verified(用户赞) |
| **新增** `incident_writer.py` | P2-① | 校验失败/用户踩→写入结构化 incident 事件 |
| **新增** `incident_analyzer.py` | P2-① | 反馈分析器：自动扫描 pending→分类根因→输出修复方案 |
| **新增** `regression_test.py` | P2-② | 回归测试系统：历史回放+SQL对比+报告生成 |
| **改造** `safe-restart.sh` | P2-② | 新增 `--regtest` 参数，重启后自动回归测试 |
| **新增** `cross_validator.py` | P3 | 结果交叉验证：锚点查询+维度之和偏差>5%报警 |
| **改造** `models.py` | 修复 | `QueryInfo.source` Literal 补 `template_recovered`/`llm_recovered` |
| **改造** `sql_validator.py` | 修复 | 成交口径校验正确区分"未成交"(查='否') vs "成交"(查='是') |

#### 7 层防御技术细节

| 层 | 方案 | 触发条件 | 效果 |
|----|------|---------|------|
| P0-① | 条件类型归一化 | 意图拆解输出 drug_* 子类型 | 匹配不上模板→归一化为 drug_any→命中通用模板 |
| P0-② | 自动恢复 | 校验失败 | 自动重试 1-2 次→通过则标记 recovered→失败保留 warning |
| P1-① | 药品上下文注入 | 涉及药品条件 | LLM 知道字段映射+已知药品名，减少幻觉 |
| P1-② | 缓存信任等级 | 写入/命中缓存 | ephemeral 跳过→Confirmed 正常→verified 持久 |
| P2-① | 反馈闭环 | 用户踩/校验失败 | incident→AI分析→出方案→先生确认→修正 |
| P2-② | 回归测试 | `--regtest` | 回放历史查询对比 SQL+行数→发现回归及时报警 |
| P3 | 交叉验证 | 每条新查询 | 锚点比对→维度之和 vs 全量偏差>5%→warning |

#### 部署说明
- 全天遵循"测试→验证→同步正式→`--regtest`→安全重启"流程
- 回归测试抓到 3 个 `template_recovered` 500 报错，已修复
- 正式环境 PID 1893389，全量 953,762 行，回归测试 7/7 通过