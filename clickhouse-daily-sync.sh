#!/bin/bash
# ============================================================
# 星宝 ClickHouse 每日数据同步
# 每天 6:00 执行（同事 5:20 清洗前一天数据）
# ============================================================
set -e

STAR_QUERY_DIR="/root/.lightclaw/workspace/star-query"
LOG_FILE="/var/log/clickhouse-daily-sync.log"
PYTHON=/root/.lightclaw/venv/bin/python3

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始增量同步..." >> "$LOG_FILE"

# Step 1: 从 ClickHouse 拉取昨日增量数据
cd "$STAR_QUERY_DIR"
$PYTHON clickhouse-sync.py --mode daily >> "$LOG_FILE" 2>&1

# Step 2: 合并增量到全量 parquet（去重：按场景ID保留最新）
YESTERDAY=$(date -d 'yesterday' '+%Y-%m-%d')
FULL_FILE="/root/All_data_ch_full.parquet"
INCR_FILE="$STAR_QUERY_DIR/data/增量_${YESTERDAY}.parquet"
MERGED_FILE="/root/All_data_ch_full_merged.parquet"

$PYTHON -c "
import pandas as pd
import sys

full = pd.read_parquet('$FULL_FILE')
incr = pd.read_parquet('$INCR_FILE')
print(f'  全量: {len(full):,} 行 | 增量{len(incr):,} 行')

# 解析增量日期（从增量 parquet 的 ydate 中获取）
incr_ydates = incr['ydate'].unique()
print(f'  增量日期范围: {sorted(incr_ydates)}')

# Step A: 从全量中移除增量范围内的旧数据（这些会被增量覆盖）
before_remove = len(full)
full = full[~full['ydate'].isin(incr_ydates)]
removed = before_remove - len(full)
print(f'  全量移除增量日期数据: {removed:,} 行')

# Step B: 合并（此时不会有真正重复的行，因为增量日期已被移除）
# 注意：不能按 场景ID 去重，因为 ARRAY JOIN 展开后同一场景ID会有多行（每种疾病一行）
merged = pd.concat([full, incr], ignore_index=True)
print(f'  合并后: {len(merged):,} 行')

# 验证多疾病展开是否保留
scene_counts = merged.groupby('场景ID').size()
multi = scene_counts[scene_counts > 1]
print(f'  多疾病场景数: {len(multi):,} (涉及 {multi.sum():,} 行)')

# 日期范围
print(f'  日期: {merged[\"ydate\"].min()} ~ {merged[\"ydate\"].max()} ({merged[\"ydate\"].nunique()} 天)')

merged.to_parquet('$MERGED_FILE', index=False)
import os
print(f'  文件大小: {os.path.getsize(\"$MERGED_FILE\")/1024/1024:.1f} MB')
"

# 原子替换（避免读取到不完整的文件）
mv "$MERGED_FILE" "$FULL_FILE"
echo "  ✅ 已合并增量数据到全量文件" >> "$LOG_FILE"

# Step 3: 更新正式环境行数
TOTAL_ROWS=$($PYTHON -c "import pandas as pd; print(len(pd.read_parquet('/root/All_data_ch_full.parquet')))")
sed -i "s|\"total_rows\": [0-9]*|\"total_rows\": $TOTAL_ROWS|" "$STAR_QUERY_DIR/backend/schema_knowledge.py"
echo "  ✅ 更新正式 schema_knowledge: total_rows=$TOTAL_ROWS" >> "$LOG_FILE"

# Step 3.5: 重建正式环境持久化 DuckDB（正式环境用 .duckdb 文件，重启时秒开）
echo "  📋 重建持久化 DuckDB..." >> "$LOG_FILE"
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
echo "  ✅ 持久化 DuckDB 已重建" >> "$LOG_FILE"

# Step 4: 重启正式环境
cd "$STAR_QUERY_DIR" && bash safe-restart.sh --prod >> "$LOG_FILE" 2>&1

# ---- 同步到测试环境 ----
TEST_DIR="/root/.lightclaw/workspace/star-query-test"
TEST_FULL_FILE="/root/All_data_ch_full_test.parquet"

echo "  📋 同步全量文件到测试环境..." >> "$LOG_FILE"
cp "$FULL_FILE" "$TEST_FULL_FILE"
echo "  ✅ 测试环境全量文件已更新" >> "$LOG_FILE"

# 更新测试环境的 schema_knowledge 行数
sed -i "s|\"total_rows\": [0-9]*|\"total_rows\": $TOTAL_ROWS|" "$TEST_DIR/backend/schema_knowledge.py"
echo "  ✅ 更新测试 schema_knowledge: total_rows=$TOTAL_ROWS" >> "$LOG_FILE"

# 重启测试环境
cd "$TEST_DIR" && bash safe-restart.sh --test >> "$LOG_FILE" 2>&1
echo "  ✅ 测试环境已重启" >> "$LOG_FILE"
# ----

# ---- 药品映射表增量更新 ----
echo "  📋 开始药品映射表增量更新..." >> "$LOG_FILE"
MAPPING_FILE="/tmp/star-mapping/results/星宝药品ATC映射表_v1.xlsx"
INCR_SCRIPT="/tmp/star-mapping/scripts/run_incremental.py"

if [ -f "$INCR_SCRIPT" ] && [ -f "$MAPPING_FILE" ]; then
    $PYTHON "$INCR_SCRIPT" --new-parquet "$FULL_FILE" --mapping "$MAPPING_FILE" >> "$LOG_FILE" 2>&1
    echo "  ✅ 映射表增量更新完成" >> "$LOG_FILE"

    # 通知正式环境和测试环境热加载新映射（不重启）
    echo "  📋 通知环境热加载新映射表..." >> "$LOG_FILE"
    ADMIN_TOKEN=$($PYTHON -c "
import requests, json
r = requests.post('http://localhost:8000/api/auth/login', 
    json={'username':'admin','password':'admin888'})
print(json.loads(r.text)['token'])
" 2>/dev/null)
    curl -s -X POST http://localhost:8000/api/admin/reload-mapping \
        -H "Authorization: Bearer $ADMIN_TOKEN" -o /dev/null
    curl -s -X POST http://localhost:8002/api/admin/reload-mapping \
        -H "Authorization: Bearer $ADMIN_TOKEN" -o /dev/null
    echo "  ✅ 正式/测试环境映射表已热加载" >> "$LOG_FILE"
else
    echo "  ⚠️ 增量脚本或映射表文件不存在，跳过增量更新" >> "$LOG_FILE"
fi
# ----

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 同步完成 ✅" >> "$LOG_FILE"
