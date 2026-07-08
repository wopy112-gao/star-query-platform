# 星宝平台健壮度优化方案

**日期：** 2026-07-08
**状态：** 待执行

---

## 背景

星宝平台存在内存占用过高（两个实例共占 5.4GB，可用内存仅 137MB/7.5GB）和无进程守护（崩溃后无法自动恢复）的问题，导致平台"脆弱、容易宕机"。

---

## 方案总览

| 步骤 | 内容 | 涉及文件 | 状态 |
|------|------|---------|------|
| 第1步 | 正式环境：DuckDB 持久化 + 内存上限 2GB | `sql_engine.py` | ✅ 已完成 |
| 第2步 | 测试环境：内存上限 1GB，沿用 `:memory:` | `sql_engine.py`（环境分支） | ✅ 已完成 |
| 第3步 | 数据同步脚本：每日重建 .duckdb 文件 | `clickhouse-daily-sync.sh` | ✅ 已完成 |
| 第4步 | systemd 进程守护 | 新建两个 `.service` 文件 | ✅ 已完成 |
| 第5步 | `safe-restart.sh` 对接 systemctl | `safe-restart.sh`（两个环境） | ✅ 已完成 |
| 第6步 | 正式环境部署应用 | 同步全部改动 | 待执行 |

---

## 第1步：正式环境 DuckDB 持久化 + 内存上限

### 改动点：`backend/sql_engine.py`

**改造目标：**
- 从 `:memory:` 改为持久化 `.duckdb` 文件
- 设内存上限 2GB（`SET memory_limit='2GB'`）
- 设查询超时 15 秒（`SET max_execution_time='15s'`）
- 首次加载时从 parquet 导入，后续启动秒开

**环境区分策略：**
- 正式环境（端口 8000）：读取 `.env` 中的 `STARQUERY_DB_MODE=persistent`，使用 `/tmp/star-query.duckdb`
- 测试环境（端口 8002）：不设该环境变量，沿用 `:memory:` 模式

**代码逻辑：**

```python
# 环境配置
USE_PERSISTENT = os.getenv("STARQUERY_DB_MODE") == "persistent"

if USE_PERSISTENT:
    DB_FILE = "/tmp/star-query.duckdb"
    self._conn = duckdb.connect(DB_FILE)
    self._conn.execute("SET memory_limit='2GB'")
    self._conn.execute("SET max_execution_time='15s'")
    
    # 检查表是否存在
    exists = self._conn.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name='data'"
    ).fetchone()[0]
    
    if not exists:
        self._conn.execute(
            f"CREATE TABLE data AS SELECT * FROM read_parquet('{data_path}')"
        )
        self._row_count = ...
else:
    # 测试环境：沿用原有的 :memory: 模式
    self._conn = duckdb.connect(":memory:")
    self._conn.execute("SET memory_limit='1GB'")
    self._conn.execute("SET max_execution_time='15s'")
    self._conn.execute(
        f"CREATE TABLE data AS SELECT * FROM read_parquet('{data_path}')"
    )
    self._row_count = ...
```

### 涉及修改的其他文件

- `star-query/.env` — 加 `STARQUERY_DB_MODE=persistent`
- `star-query-test/.env` — 不加（沿用默认）
- `backend/app.py` — 增加适量 import（如 `os` 已引入）

---

## 第2步：测试环境内存上限（沿用 `:memory:`）

测试环境不设持久化，只设内存上限 1GB + 查询超时 15s。不做其他改动。

---

## 第3步：数据同步脚本

### 改动点：`clickhouse-daily-sync.sh`

在 Step 2（合并 parquet → 原子替换）之后，加一步重建 .duckdb 文件：

```bash
# Step 2.5: 重建持久化 DuckDB（正式环境使用）
if [ -f "/tmp/star-query.duckdb" ]; then
    rm -f "/tmp/star-query.duckdb"
    echo "  ✅ 已清除旧 DuckDB 缓存，下次启动自动重建"
fi
```

如果不想重启时重建（启动慢），也可以主动重建：

```bash
$PYTHON -c "
import duckdb
conn = duckdb.connect('/tmp/star-query.duckdb')
conn.execute(\"SET memory_limit='2GB'\")
conn.execute(\"CREATE TABLE data AS SELECT * FROM read_parquet('/root/All_data_ch_full.parquet')\")
rows = conn.execute('SELECT count(*) FROM data').fetchone()[0]
print(f'  重建 DuckDB 持久化文件: {rows:,} 行')
conn.close()
"
```

后面这个方案更优——数据同步完成后立即重建，服务重启时直接打开已有文件，启动时间从 4.4 秒降到 <0.1 秒。

---

## 第4步：systemd 进程守护

### 新建文件：`/etc/systemd/system/star-query-prod.service`

```ini
[Unit]
Description=星宝数据查询系统 - 生产环境
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/.lightclaw/workspace/star-query/backend
EnvironmentFile=/root/.lightclaw/workspace/star-query/.env
ExecStart=/root/.lightclaw/venv/bin/python3 app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 新建文件：`/etc/systemd/system/star-query-test.service`

同上，但：
- Description 改为测试环境
- WorkingDirectory 指向 `star-query-test/backend`
- EnvironmentFile 指向 `star-query-test/.env`

### 服务管理命令

```bash
systemctl daemon-reload
systemctl enable star-query-prod
systemctl enable star-query-test
systemctl start star-query-prod
systemctl start star-query-test
systemctl status star-query-prod
```

### 每日同步后重启方式变更

`clickhouse-daily-sync.sh` 中的重启命令改为：
```bash
systemctl restart star-query-prod
```

---

## 第5步：safe-restart.sh 对接 systemctl

`safe-restart.sh` 的 Step 3-5（手动 nohup + 健康检查）改为：

```bash
systemctl restart star-query-${ENV_LOWER}
# 用 systemctl is-active 等待
```

保留旧逻辑作为回退（fallback），通过 `--legacy` 参数切换。

---

## 预期效果

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| 单个实例内存 | 2.7GB | ≤2GB（可控） |
| 两个实例合计 | 5.4GB | ≤4GB |
| 系统可用内存 | 137MB | ~2.5GB+ |
| 启动时间 | 4.4 秒 | <0.1 秒（持久化模式） |
| 进程崩溃恢复 | ❌ 需手动 | ✅ systemd 自动拉起 |
| 慢查询保护 | ❌ 无 | ✅ 15 秒自动断 |
| 开机自启 | ❌ 无 | ✅ 自动恢复 |

## 查询超时实现（DuckDB v1.5.2 限制）

DuckDB 无内置 `SET max_execution_time`，使用 `conn.interrupt()` + `threading.Timer` 在 Python 层实现。

**状态：✅ 已实现**

已在 `sql_engine.py` 中新增 `_execute_with_timeout()` 方法，所有用户查询经过 15 秒超时保护。
超时触发时抛出 `InterruptException`，被统一的 `except Exception` 捕获，返回友好错误信息。已测试验证通过（3秒超时测试，笛卡尔积慢查询被成功中断）。
