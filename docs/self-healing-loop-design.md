# 星宝纠错自愈系统设计方案

> 版本：v1.1
> 日期：2026-07-23
> 状态：已实施（2026-07-24 同步到正式环境）

> **实施状态：** P0（链路打通）✅ P1（安全锁）✅ P2（异步自愈）✅ P4（断路器+循环检测）✅ 已全部同步到正式环境。
> **待办：** 正式环境添加 scan_incidents.py cron（目前测试环境有 30s cron，正式环境无）
>
> **变更说明：** v1.1 基于代码审阅做了 8 项优化——复用 query_cache 现有信任机制、
> 以 SQLite incidents 表为事件主线、增强 incident_analyzer 而非 YAML 化、
> 异步自愈为主同步兜底为辅、增量构建评估层、具体化 cron 触发机制、
> 强制所有代码修改走 fix_applier、新增同步事务管理器。

---

## 一、背景与问题

### 1.1 当前链路状态

```
QClaw/用户点踩 → incident JSON (✅ 有数据)
               → Agent 分析根因 → 生成修复方案 (❌ 无人/断链)
               → fix_applier 自动应用 (无方案可执行，7种修复动作全部闲置)
```

- `feedback_review/`：5月26日 ~ 6月12日，持续有 incident 写入
- `fix_proposals/`：**最后方案 6月5日**，至今两个月只进不出
- 纠错 4 个模块（incident_writer / feedback_store / incident_analyzer / fix_applier）各自独立，链路断路

### 1.2 六个缺陷

| # | 缺陷 | 影响 |
|---|------|------|
| 1 | 链路断裂——incident→方案生成无人做 | 事件堆积两个月 |
| 2 | 方案生成依赖 Agent 手动分析 | 认知偏差、不可重复、依赖在线 |
| 3 | 事件无 ACK / 状态机 | 失败事件无法重试，处理/未处理混在目录里 |
| 4 | 无量化度量 | 无法回答"纠错系统效果如何" |
| 5 | 触发方式不合理 | 心跳轮询（延迟大），非事件驱动 |
| 6 | fix_applier 能力闲置 | 备份/回滚/回归验证/分级处理全部没用上 |

### 1.3 代码现状（v1.1 新增：实际能力盘点）

| 模块 | 已有能力 | 方案需注意 |
|------|---------|-----------|
| `query_cache.py` | 三层缓存 + intent_key + trust_level（verified/confirmed/ephemeral），点赞升级/点踩降级 | **复用现有信任机制，不新建 Gold Standard 表** |
| `incident_writer.py` | 写 JSON + 双写 SQLite incidents 表 | **以 SQLite 为主线，JSON 为辅** |
| `incident_analyzer.py` | 7 种错误分类 + 3 种 user_dislike 子模式 + 自动修复（add_few_shot/add_condition_type等） | **增强即可，不做 YAML 规则引擎** |
| `fix_applier.py` | 完整的分级处理（high/medium/low）、备份/回滚、回归验证、通知生成 | **所有代码修改必须走此流程** |
| `feedback_store.py` | query_feedback 表，赞/踩记录 | 数据量小，评估层增量构建 |
| `sql_validator.py` | 维度校验 + 合理性校验 + 时间一致性校验 + 意图一致性校验 | 回归验证已有的轻量评估 |
| `admin_store.py` | incidents 表 CRUD + 统计 + 修复状态跟踪 | 事件总线靠它 |
| `sql_engine.py` | DuckDB 连接池、药品 LIKE/UNNEST 自动改写 | 框架稳定，不涉及改动 |

---

## 二、四种行业模式参考

### 模式 1：Self-Healing SQL Pipeline（arXiv 2604.16511）

| 要素 | 描述 | 星宝映射 |
|------|------|---------|
| 两阶段管线 | SQL 生成 和 SQL 评估/修复 分离 | query_router → fix_applier |
| 自愈循环 | 捕获执行错误 → 反馈给 LLM 迭代修正 | **异步自愈为主**（见 4.1 节细化） |
| 防回归 | 提前接受 + 最佳结果追踪 | 修复后回归验证（fix_applier 已有） |
| 效果 | Spider/BIRD 准确率 +4.6~9.3pp | 预期减少 60% 报错 |

### 模式 2：Closed-Loop RAG（三层自愈架构）

| 层 | 功能 | 星宝映射 |
|----|------|---------|
| L1 输入层 Query Correction | 修正用户问题本身（拼写/同义词/口径） | 问题预处理（query_intent） |
| L2 检索层 Corrective RAG | 验证并过滤检索结果，不相关则丢弃/重查 | SQL 验证（sql_validator）+ 异步修复 |
| L3 学习层 Dynamic Few-Shot | 点赞查询→Gold Standard→下次自动注入 | **query_cache.trust_level 现有链** |

### 模式 3：Self-Healing Data Pipeline

四步闭环：**Detect → Diagnose → Fix → Learn**

这一模式作为整体架构骨架，见第三节。

### 模式 4：Evaluation-Driven Development

> "You cannot scale agent systems without evaluation."

- QClaw 每天点踩数据 → **增量构建**评估数据集（见 4.6 节细化）
- 每次修复前跑回归 → fix_applier._regression_verify() 已有
- 核心指标：修复率、回滚率、自愈覆盖率

---

## 三、架构设计：四步闭环（v1.1 更新版）

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        四步闭环架构 (Self-Healing) v1.1                           │
│                                                                                 │
│  ┌──────────────────┐    ┌───────────────┐    ┌─────────────────┐               │
│  │  DETECT（检测）    │    │ DIAGNOSE（诊断）│    │   FIX（修复）    │               │
│  │                   │    │               │    │                 │               │
│  │ SQL执行错误       │    │ incident_     │    │ fix_applier:    │               │
│  │  → 写incident     │───▶│ analyzer:     │───▶│  备份 → 应用     │               │
│  │ 用户点踩          │    │  分类错误类型   │    │  → 回归验证      │               │
│  │  → 写incident     │    │  对比intent    │    │  → 回滚/确认     │               │
│  │ 校验器失败        │    │  生成方案       │    │  → 通知用户      │               │
│  │  → 写incident     │    │  ★不走YAML化   │    │  ★所有改代码必   │               │
│  │ 异步自愈报告      │    │               │    │  须经此流程      │               │
│  └────────┬─────────┘    └───────┬───────┘    └───────┬──────────┘               │
│           │                      │                     │                          │
│           ▼                      ▼                     ▼                          │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │                    LEARN（学习）★ v1.1 简化版                                │  │
│  │                                                                           │  │
│  │  方案 v1.0 设计的 gold_standard 表 → 废弃 ← 改用 query_cache 现有机制：      │  │
│  │                                                                           │  │
│  │  点赞查询 → upgrade_trust → verified（已有：query_cache.py 第 112 行）        │  │
│  │  点踩查询 → downgrade_trust → ephemeral（已有：query_cache.py 第 138 行）     │  │
│  │  修复成功 → store_with_intent(..., trust_level="verified")                  │  │
│  │  下次命中 → lookup_by_intent(key, min_trust="confirmed")                    │  │
│  │                                                                           │  │
│  │  新错误模式成功修复3次 → 自动在 llm_translator.py 中生成新 few-shot 示例       │  │
│  │  （由 incident_analyzer 分析 + fix_applier 执行）                           │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  触发层：cron 每30s → scan_incidents.py → SQLite查询pending事件 → 处理    │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │  安全层：所有代码修改 → 强制走 fix_applier（备份→应用→回归→确认/回滚）      │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 四、模块详细设计（v1.1 优化版）

### 4.1 DETECT — SQL 执行错误捕获（异步自愈为主）

**位置：** `query_router.py` 中 `engine.execute()` 出错时捕获

**v1.1 重要变化：** 自愈循环改为**异步为主，同步兜底为辅**。不在用户请求路径上串行等待 3 轮 LLM。

```
用户查询 → LLM翻译 → SQL → 沙箱执行
               │
          ┌────┴────┐
          ▼         ▼
        成功      失败
          │         │
          │         ├─→ 同步快速兜底（仅1轮）
          │         │    重试条件：error 类型已知可自动修正
          │         │    （如语法错误→加括号、字段缺失→加引号）
          │         │    成功 → 返回 + 记录自愈
          │         │    失败 → 响应"查询暂时失败，正在后台修复"
          │         │
          │         └─→ 写入 SQLite incidents（status='pending'）
          │               → cron 扫描 → 异步处理
          │
          ▼
      返回结果（用户无感）
```

**调用接口：**

```python
def heal(question: str, sql: str, error: str) -> Optional[str]:
    """异步自愈：写入 incident，返回 None 表示"走后台自愈"。
    
    只在 error 类型明确可自动修正时做 1 轮同步重试。
    """
```

### 4.2 DETECT — 事件源统一（v1.1 简化版：以 SQLite 为主线）

**v1.1 重要变化：** 废弃 `incident_pipe.py` 设计。事件源统一写入 SQLite `incidents` 表。

| 事件源 | 写入方式 |
|--------|---------|
| SQL 执行错误 | `incident_writer.write_incident(type="sql_error", ...)` → **已双写** JSON + SQLite |
| 校验器告警 | `incident_writer.write_incident(type="validation_fail", ...)` → **已有** |
| 用户点踩（前端） | `post_feedback()` 中 `write_incident(type="user_dislike", ...)` → **已有** |
| QClaw 每天点踩 | feedback_store → `write_incident()` → **已有** |
| 结果异常（空结果等） | 在 query_router 中增加捕获 → **新增写入点** |

**状态机（基于 SQLite 实现）：**

```
     ┌─── fix_applier 重试(≤3次) ───┐
     ▼                               │
pending → analyzing → resolved (回归验证通过)
     │
     ├──→ failed (回滚触发)
     │
     └──→ deferred (需人工审核)
```

**ACK 机制：** 无需额外实现。cron 每次扫描时直接查 `WHERE status='pending' AND (created_at < datetime('now', '-1 hour') OR retries < 3)`，天然支持重试。

### 4.3 DIAGNOSE — incident_analyzer 增强（v1.1 不做 YAML 化）

**v1.1 重要变化：** 不在 incident_analyzer 之上新建 `rule_engine.py` + YAML 规则层。直接在现有 `incident_analyzer.py` 中增强：

#### 4.3.1 增强内容

| 功能 | 当前状态（已有） | 增强方式 |
|------|----------------|---------|
| 错误分类 | `_classify_error_type()`: 7 种类型 | 扩展错误类型（如新增"时间条件缺失"分类） |
| user_dislike 分析 | `_analyze_user_dislike_with_sql()`: 3 种模式 | 继续扩展模式库 |
| 意图对比 | `_inspect_intent_vs_sql()` | 保持现有状态 |
| 分组 | `group_similar()` | 保持 |
| 方案生成 | `generate_proposal()` + `save_proposal()` | 保持，但增加断路器逻辑 |
| 自动修复 | `apply_proposal()` **直接改代码** | **→ 改为写入 proposal JSON，由 fix_applier 执行** |

#### 4.3.2 断路器（新增至 incident_analyzer）

```
同类型 incident 连续 3 次修复失败
  → 标记为 low 置信度（不再自动修复）
  → 在 proposal 中增加 "exception: true" 标记
  → fix_applier 遇到 exception 标记时跳过自动应用，直接通知
```

#### 4.3.3 循环检测（新增至 incident_analyzer）

```
扫描 SQLite incidents: type='user_dislike' AND created_at > datetime('now', '-7 days')
  → 同 question 出现 ≥2 次 → 标记 recurring
  → 原修复方案置信度降级
  → 检查 query_cache 中 trust_level 是否未变化
```

### 4.4 FIX — fix_applier 作为唯一入口

**v1.1 重要变化：** 所有代码修改（包括 incident_analyzer 生成的方案）必须走 fix_applier 流程，不再允许直接修改代码文件。

#### 4.4.1 强制流程

```
incident_analyzer 分析 → 生成 proposal JSON（含 proposed_changes）
  → fix_applier 扫描到 pending_review 的 proposal
    → 1. 备份改动涉及的文件
    → 2. 应用变更
    → 3. 回归验证（基于 fix_applier._regression_verify 已有逻辑）
    → 4a. 通过 → 标记 applied + 固化
    → 4b. 失败 → 自动回滚 + 标记 failed
```

#### 4.4.2 分级执行策略（保持现有逻辑）

| 置信度 | 策略 |
|--------|------|
| high | 自动应用 + 回归验证 + 固化 Gold Standard（= 写入 query_cache verified） |
| medium | 自动生成方案 + 标记待审 + 暂不应用 |
| low | 自动归集 + 周报汇总 |
| exception | 断路器标记 → 跳过自动应用 → 通知人工 |

#### 4.4.3 回归验证增强

基于 `fix_applier._regression_verify()` 已有的 5 项验证：

```
1. ✅ SQL 语法正确（_is_valid_sql 已有）
2. ✅ 执行不报错（仅修复代码时适用，SQL 修复：依赖回归测试重跑）
3. ✅ 结果非空（查询类问题）
4. ✅ 新旧 SQL 不同（_sql_eq 已有）
5. ✅ 旧警告中错误模式已消除（_warning_still_present 已有）
```

#### 4.4.4 新增：安全锁

生产环境禁止直接改代码。如果在生产环境检测到 `incident_analyzer.apply_proposal()` 被调用，抛出异常提示"必须经 fix_applier 执行"。

```python
# 在 incident_analyzer.py 中增加守卫
def _guard_safe_mode():
    """生产环境禁止直接改代码"""
    import os
    if os.getenv("STARQUERY_DB_MODE") == "persistent":
        raise RuntimeError("安全锁：生产环境禁止直接修改代码，请经 fix_applier 执行")
```

### 4.5 LEARN — Gold Standard 闭环（v1.1 统一到 query_cache）

**v1.1 重要变化：** 废弃 v1.0 设计的 `gold_standard` 表。所有学习闭环复用 `query_cache.py` 的现有信任机制。

#### 4.5.1 现有信任等级链

```
ephemeral（临时，LLM fallback 生成）
    → confirmed（模板匹配生成，信任）
        → verified（用户点赞/人工/修复确认，永久）
```

`query_cache.py` 已实现：
- `store_with_intent(sql, intent_key, trust_level)` — 带等级写入
- `upgrade_trust(question)` — 点赞升级
- `downgrade_trust(question)` — 点踩降级
- `lookup_by_intent(key, min_trust)` — 按等级过滤

#### 4.5.2 正向路径（点赞）— 已有

```
用户点赞查询
  → post_feedback() 中 upgrade_trust(question, by_intent_key=True)
    → trust_level 提升
      → 下次同类查询命中 verified 缓存
```

#### 4.5.3 反向路径（点踩→修复）— 优化

```
用户点踩 / SQL 报错
  → incident → incident_analyzer 分析 → fix_applier 修复
    → 修复后的 SQL 通过 store_with_intent(sql, intent_key, trust_level="verified") 写入
      → query_cache 中该 intent_key 的缓存被升级
```

#### 4.5.4 规则自我进化

同一错误模式成功修复 3 次以上 → incident_analyzer 累积到足够多的成功案例后，生成一条新的 few-shot 示例 → 写入 proposal JSON → fix_applier 执行（追加到 llm_translator.py 的 CATEGORIZED_EXAMPLES 中）。

### 4.6 评估层 — 增量构建（v1.1 务实版）

**v1.1 重要变化：** 不做全量评估集构建。目前点踩/点赞数据量很小，全量构建意义有限。

#### 4.6.1 增量策略

```
fix_applier 修复前
  → 跑 fix_applier._regression_verify()（基于现有验证逻辑）
    → 记录基线
      → 应用修复
        → 再跑验证 → 对比
          ├── 通过 ✅ → 确认
          └── 失败 ❌ → 回滚
```

#### 4.6.2 评估集按需扩展

当 `query_feedback` 表中数据量累计超过 100 条时，再在 incident_analyzer 中自动构建评估集：

```python
def build_eval_set_if_needed():
    """检查数据量，>100 条时构建评估集"""
    stats = get_feedback_stats()
    if stats["total"] >= 100:
        # 增量构建
        pass
```

#### 4.6.3 核心指标（精简到 3 个）

| 指标 | 计算 | 目标 |
|------|------|------|
| 自愈覆盖率 | 自愈成功次数 / 总报错次数 | >60% |
| 自动修复率 | resolved / total incidents | >40% |
| 回滚率 | rolled_back / applied | <20% |

**移除的指标（v1.0 设计了 6 个）：**
- 修复正确率 → 被回滚率覆盖
- GS 增长率 → 点踩/点赞数据量太小，统计不具意义
- 平均修复时间 (MTTR) → 无用户可操作的 action

---

## 五、触发机制（v1.1 具体化）

**v1.1 重要变化：** 从概念化的"事件驱动为主+心跳兜底"具体化为可执行的 cron 方案。

```
cron 每 30 秒
  → scan_incidents.py
    → SELECT * FROM incidents WHERE status='pending'
      AND (fix_attempted_at IS NULL OR fix_attempted_at < datetime('now', '-1 hour'))
      AND retries < 3
    → 有结果 → 调用 incident_analyzer.scan_and_analyze()
      → 生成 proposal JSON
        → 调用 fix_applier.run()
          → 分级执行
            → 通知回调（飞书/微信）
    → 无结果 → 静默退出（0 token 消耗）
```

**实现文件：** `scripts/scan_incidents.py`

```python
"""cron 入口：扫描 pending incidents → 分析 → 修复 → 通知"""
def main():
    incidents = get_pending_incidents()  # 查 SQLite
    if not incidents:
        return  # 无事件，静默退出
    
    proposals = incident_analyzer.scan_and_analyze()
    for proposal in proposals:
        fix_applier.process(proposal)  # 强制走 fix_applier
```

**cron 任务：**

```bash
# crontab（每 30s 执行一次）
* * * * * for i in 0 30; do sleep $i; python3 /path/to/scripts/scan_incidents.py; done
```

---

## 六、同步事务管理器（v1.1 新增）

**问题：** `clickhouse-daily-sync.sh` 线性执行 parquet → schema → restart，中间失败导致不一致。

**方案：** 新增状态文件追踪的事务机制。

**实现文件：** `scripts/sync-transaction.sh`

```
状态机：
  IDLE → STAGING → VALIDATED → BACKUP → DEPLOY → RESTART → CONFIRM → COMPLETE
                    │                          │                      │
                    └→ FAILED_CLEANUP ←────────┘                      │
                                                                      └→ ROLLBACK

状态文件：/tmp/star-sync-state.json
{
  "phase": "STAGING",
  "started_at": "2026-07-23T10:00:00",
  "source_hash": "abc123",
  "backup_path": "/path/to/backup",
  "error": null
}
```

所有阶段可中断重启。重启时读取状态文件，从断点继续或回滚。

---

## 七、实施路径（v1.1 调整版）

| 阶段 | 内容 | 预估 | 独立可用？ |
|------|------|------|-----------|
| **P0** | scan_incidents.py + cron 触发 + incident_analyzer 增强 | 1天 | ✅ 链路打通，事件不再堆积 |
| **P1** | 强制所有代码修改走 fix_applier + 安全锁 | 0.5天 | ✅ 杜绝直接改代码风险 |
| **P2** | 异步自愈（query_router 异常→写 incident→后台处理）| 0.5天 | ✅ 用户查询失败无感恢复 |
| **P3** | sync-transaction.sh 同步事务管理器 | 1天 | ✅ 数据同步可靠性 |
| **P4** | 增量评估 + 断路器 + 循环检测 | 0.5天 | ✅ 纠错系统自我监控 |

**建议 P0 优先：** 一句话可概括——让 cron 每 30s 去 SQLite 查一次有没有 pending 的 incident，有就自动分析和修复。这是链路断裂的解决核心。P1-P4 按需推进。
