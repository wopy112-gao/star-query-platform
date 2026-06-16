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

# 合并 + 按场景ID去重（保留后出现的，即增量优先）
merged = pd.concat([full, incr], ignore_index=True)
before = len(merged)
merged = merged.drop_duplicates(subset=['场景ID'], keep='last')
after = len(merged)
print(f'  合并后: {before:,} → 去重后: {after:,} 行 (删除 {before-after:,} 个重复场景)')

# 日期范围
print(f'  日期: {merged.ydate.min()} ~ {merged.ydate.max()} ({merged.ydate.nunique()} 天)')

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

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 同步完成 ✅" >> "$LOG_FILE"
