#!/bin/bash
# ============================================================
# 星宝 ClickHouse 每日数据同步
# 每天 6:00 执行（同事 5:20 清洗前一天数据）
#
# v2 — 改造：运行时增量加载，不重启服务
# 全量 parquet 保留（用于灾难恢复），DuckDB 通过 API 运行时更新
# ============================================================
set -e

STAR_QUERY_DIR="/root/.lightclaw/workspace/star-query"
LOG_FILE="/var/log/clickhouse-daily-sync.log"
PYTHON=/root/.lightclaw/venv/bin/python3

# 加载 .env 环境变量（凭证集中管理，不入代码）
set -a; source "$STAR_QUERY_DIR/.env"; set +a

echo "[$(date '+%Y-%m-%d %H:%M:%S')] =========================" >> "$LOG_FILE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始增量同步..." >> "$LOG_FILE"

# ---- Step 1: 从 ClickHouse 拉取昨日增量数据 ----
cd "$STAR_QUERY_DIR"
$PYTHON clickhouse-sync.py --mode daily >> "$LOG_FILE" 2>&1

# ---- Step 2: 合并增量到全量 parquet ----
YESTERDAY=$(date -d 'yesterday' '+%Y-%m-%d')
FULL_FILE="/root/All_data_ch_full.parquet"
INCR_FILE="$STAR_QUERY_DIR/data/增量_${YESTERDAY}.parquet"
MERGED_FILE="/root/All_data_ch_full_merged.parquet"

echo "  📋 合并全量 parquet..." >> "$LOG_FILE"

$PYTHON -c "
import pandas as pd
import sys

full = pd.read_parquet('$FULL_FILE')
incr = pd.read_parquet('$INCR_FILE')
print(f'  全量: {len(full):,} 行 | 增量: {len(incr):,} 行')

incr_ydates = incr['ydate'].unique()
print(f'  增量日期范围: {sorted(incr_ydates)}')

before_remove = len(full)
full = full[~full['ydate'].isin(incr_ydates)]
removed = before_remove - len(full)
print(f'  全量移除增量日期数据: {removed:,} 行')

merged = pd.concat([full, incr], ignore_index=True)
print(f'  合并后: {len(merged):,} 行')

scene_counts = merged.groupby('场景ID').size()
multi = scene_counts[scene_counts > 1]
print(f'  多疾病场景数: {len(multi):,} (涉及 {multi.sum():,} 行)')

print(f'  日期: {merged[\"ydate\"].min()} ~ {merged[\"ydate\"].max()} ({merged[\"ydate\"].nunique()} 天)')

merged.to_parquet('$MERGED_FILE', index=False)
import os
print(f'  文件大小: {os.path.getsize(\"$MERGED_FILE\")/1024/1024:.1f} MB')
" >> "$LOG_FILE" 2>&1

mv "$MERGED_FILE" "$FULL_FILE"
echo "  ✅ 已合并增量数据到全量文件" >> "$LOG_FILE"

# ---- Step 3: 运行时增量加载（API，不重启） ----
# total_rows 不再用 sed 改 schema_knowledge.py，改为查询时从 engine.row_count 动态获取
echo "  📋 调用 API 增量加载..." >> "$LOG_FILE"

# 获取 Admin Token
ADMIN_TOKEN=$($PYTHON -c "
import os, requests, json
r = requests.post('http://localhost:8000/api/auth/login',
    json={'username':'admin','password': os.environ.get('ADMIN_PASSWORD', 'admin888')})
print(json.loads(r.text)['token'])
" 2>/dev/null)

if [ -z "$ADMIN_TOKEN" ]; then
    echo "  ⚠️ 无法获取 Admin Token，回退到全量重建+重启" >> "$LOG_FILE"
    FALLBACK=1
else
    # 调用增量加载 API
    API_RESULT=$(curl -s -X POST http://localhost:8000/api/admin/incremental-load \
        -H "Authorization: Bearer $ADMIN_TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"parquet_path\": \"$INCR_FILE\"}" 2>&1)

    # 检查 API 返回
    API_SUCCESS=$(echo "$API_RESULT" | $PYTHON -c "import sys,json; d=json.load(sys.stdin); print(d.get('success', False))" 2>/dev/null)

    if [ "$API_SUCCESS" = "True" ]; then
        echo "  ✅ 增量加载成功: $API_RESULT" >> "$LOG_FILE"
        FALLBACK=0
    else
        echo "  ⚠️ 增量加载 API 失败: $API_RESULT" >> "$LOG_FILE"
        echo "  ⚠️ 回退到全量重建+重启..." >> "$LOG_FILE"
        FALLBACK=1
    fi
fi

# ---- Fallback: 如果 API 增量加载失败，走旧流程 ----
if [ "$FALLBACK" = "1" ]; then
    echo "  📋 [回退] 重建持久化 DuckDB..." >> "$LOG_FILE"
    $PYTHON -c "
import duckdb, os
DB_FILE = '/tmp/star-query.duckdb'
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)
conn = duckdb.connect(DB_FILE)
conn.execute(\"SET memory_limit='2GB'\")
conn.execute(\"CREATE TABLE data AS SELECT * FROM read_parquet('/root/All_data_ch_full.parquet')\")
rows = conn.execute('SELECT count(*) FROM data').fetchone()[0]
print(f'  重建完成: {rows:,} 行')
conn.close()
" >> "$LOG_FILE" 2>&1
    echo "  ✅ [回退] 持久化 DuckDB 已重建" >> "$LOG_FILE"

    cd "$STAR_QUERY_DIR" && bash safe-restart.sh --prod >> "$LOG_FILE" 2>&1
    echo "  ✅ [回退] 正式环境已重启" >> "$LOG_FILE"
fi

# ---- 同步到测试环境 ----
TEST_DIR="/root/.lightclaw/workspace/star-query-test"
TEST_FULL_FILE="/root/All_data_ch_full_test.parquet"

echo "  📋 同步全量文件到测试环境..." >> "$LOG_FILE"
cp "$FULL_FILE" "$TEST_FULL_FILE"
echo "  ✅ 测试环境全量文件已更新" >> "$LOG_FILE"

# 测试环境也尝试增量加载
if [ -z "$ADMIN_TOKEN" ]; then
    echo "  ⚠️ 测试环境：Token 缺失，回退到重启" >> "$LOG_FILE"
    cd "$TEST_DIR" && bash safe-restart.sh --test >> "$LOG_FILE" 2>&1
else
    API_RESULT2=$(curl -s -X POST http://localhost:8002/api/admin/incremental-load \
        -H "Authorization: Bearer $ADMIN_TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"parquet_path\": \"$INCR_FILE\"}" 2>&1)

    API_SUCCESS2=$(echo "$API_RESULT2" | $PYTHON -c "import sys,json; d=json.load(sys.stdin); print(d.get('success', False))" 2>/dev/null)

    if [ "$API_SUCCESS2" = "True" ]; then
        echo "  ✅ 测试环境增量加载成功: $API_RESULT2" >> "$LOG_FILE"
    else
        echo "  ⚠️ 测试环境增量加载 API 失败，回退到重启: $API_RESULT2" >> "$LOG_FILE"
        cd "$TEST_DIR" && bash safe-restart.sh --test >> "$LOG_FILE" 2>&1
    fi
fi

# ---- 药品映射表增量更新 ----
echo "  📋 开始药品映射表增量更新..." >> "$LOG_FILE"
MAPPING_FILE="/tmp/star-mapping/results/星宝药品ATC映射表_v1.xlsx"
INCR_SCRIPT="/tmp/star-mapping/scripts/run_incremental.py"

if [ -f "$INCR_SCRIPT" ] && [ -f "$MAPPING_FILE" ]; then
    $PYTHON "$INCR_SCRIPT" --new-parquet "$FULL_FILE" --mapping "$MAPPING_FILE" >> "$LOG_FILE" 2>&1
    echo "  ✅ 映射表增量更新完成" >> "$LOG_FILE"

    echo "  📋 通知环境热加载新映射表..." >> "$LOG_FILE"
    [ -n "$ADMIN_TOKEN" ] && curl -s -X POST http://localhost:8000/api/admin/reload-mapping \
        -H "Authorization: Bearer $ADMIN_TOKEN" -o /dev/null
    [ -n "$ADMIN_TOKEN" ] && curl -s -X POST http://localhost:8002/api/admin/reload-mapping \
        -H "Authorization: Bearer $ADMIN_TOKEN" -o /dev/null
    echo "  ✅ 正式/测试环境映射表已热加载" >> "$LOG_FILE"
else
    echo "  ⚠️ 增量脚本或映射表文件不存在，跳过增量更新" >> "$LOG_FILE"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 同步完成 ✅" >> "$LOG_FILE"
