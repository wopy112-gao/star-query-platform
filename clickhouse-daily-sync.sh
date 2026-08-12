#!/bin/bash
# ============================================================
# 星宝 ClickHouse 每日数据同步 v3.1（Step 7 防复发版）
# 流程：取数 → 增量schema校验 → backfill → 合并全量 → build_db
#       → 原子替换 → 健康检查 → 失败回滚 → 增量清理
# v3.1 新增（2026-08-11 Step 7 防复发）：
#   1. 飞书告警：构建失败 / 健康检查超时 / 替换失败 / 异常退出 均推送
#   2. 增量 schema 校验：拉取后立即校验列数/必需列/基线漂移
#   3. 增量 parquet 清理：合并完成后清理 N 天前旧增量
#
# 用法：
#   clickhouse-daily-sync-v3.sh prod   # 正式+测试完整链路（cron 6:00 调用）
#   clickhouse-daily-sync-v3.sh test   # 仅测试环境构建替换（验证用）
# ============================================================
set -euo pipefail

# 补 lark-cli 路径（cron 环境 PATH 不含 node 目录，否则飞书告警发不出）
export PATH="$PATH:/usr/local/lib/nodejs/node-v24.15.0-linux-x64/bin"

STAR_QUERY_DIR="/root/.lightclaw/workspace/star-query"
TEST_DIR="/root/.lightclaw/workspace/star-query-test"
LOG_FILE="/var/log/clickhouse-daily-sync.log"
PYTHON=/root/.lightclaw/venv/bin/python3
MAPPING_FILE="/tmp/star-mapping/results/星宝药品ATC映射表_v1.xlsx"
MODE="${1:-prod}"

# ---- Step 7 防复发配置 ----
FEISHU_USER_ID="ou_65fdb77aa93d7b040915980c80723aa2"
CLEAN_KEEP_DAYS=7          # 增量 parquet 保留天数
SCHEMA_SNAPSHOT="$STAR_QUERY_DIR/data/.schema_snapshot.json"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"; }

# ---- 飞书告警（失败即通知先生，不等人工发现）----
notify_feishu() {
    local title="$1" detail="$2"
    local msg="[星宝同步] ${title}
时间: $(date '+%Y-%m-%d %H:%M:%S')
详情: ${detail}"
    log "  🔔 飞书告警: ${title}"
    if ! timeout 15 lark-cli im +messages-send --user-id "$FEISHU_USER_ID" --text "$msg" --as bot >> "$LOG_FILE" 2>&1; then
        log "  ⚠️ 飞书告警发送失败（继续执行）"
    fi
}

# 任何非 0 退出兜底告警（配合各失败点已发的具体告警）
trap 'rc=$?; if [ $rc -ne 0 ]; then notify_feishu "同步异常退出" "exit=${rc}, 日志: ${LOG_FILE}"; fi' EXIT

# ---- 离线构建 + 校验（build_db.py 自带校验，失败返回 1）----
build_and_verify() {
    local data_parquet="$1" output="$2" report="$3"
    log "  📋 离线构建新库: $output"
    if ! $PYTHON "$STAR_QUERY_DIR/scripts/build_db.py" \
        --data "$data_parquet" \
        --mapping "$MAPPING_FILE" \
        --output "$output" \
        --report "$report" \
        --memory 4GB >> "$LOG_FILE" 2>&1; then
        log "  ❌ 构建失败，保留旧库继续服务"
        notify_feishu "构建失败" "输出: ${output}, 日志: ${LOG_FILE}"
        return 1
    fi
    local status rows
    status=$($PYTHON -c "import json;print(json.load(open('$report')).get('status'))" 2>/dev/null || echo "error")
    rows=$($PYTHON -c "import json;print(json.load(open('$report')).get('tables',{}).get('data',{}).get('rows',0))" 2>/dev/null || echo "0")
    if [ "$status" != "ok" ]; then
        log "  ❌ 构建校验未通过: status=$status"
        notify_feishu "构建校验未通过" "status=${status}, 输出: ${output}"
        return 1
    fi
    log "  ✅ 构建校验通过: data=$rows 行, status=ok"
    return 0
}

# ---- 等待服务健康（先等端口，再等 health 返回 total_rows>0，最多 5 分钟）----
wait_healthy() {
    local port="$1" service="$2"
    local waited=0
    for i in $(seq 1 30); do
        # 端口未监听则继续等
        if ! ss -tlnp 2>/dev/null | grep -q ":$port "; then
            sleep 10; waited=$((waited+10))
            continue
        fi
        local rows
        rows=$(curl -s "http://localhost:$port/api/health" 2>/dev/null | \
            $PYTHON -c "import sys,json;print(json.load(sys.stdin).get('total_rows',''))" 2>/dev/null || echo "")
        if [ -n "$rows" ] && [ "$rows" != "0" ]; then
            log "  ✅ 服务健康: $service total_rows=$rows (等待${waited}s)"
            return 0
        fi
        sleep 10; waited=$((waited+10))
        log "  ⏳ 健康检查中 ($service, 已等${waited}s)..."
    done
    log "  ❌ 健康检查超时: $service 未就绪"
    notify_feishu "健康检查超时" "$service 等待${waited}s 未就绪, 日志: ${LOG_FILE}"
    return 1
}

# ---- 停止服务并等待进程完全退出（避免 mv 时旧库仍被占用）----
stop_service() {
    local service="$1"
    systemctl stop "$service" || true
    for i in $(seq 1 12); do
        if ! systemctl is-active --quiet "$service"; then
            sleep 2
            log "  ✅ 服务已停止: $service"
            return 0
        fi
        sleep 5
    done
    # 兜底：强制 kill（systemd 超时会自己处理，这里只是保险）
    log "  ⚠️ 服务未完全停止，等待 systemd 超时处理..."
    sleep 15
    return 0
}

# ---- 原子替换（停服务 → mv → 启服务 → 健康检查 → 失败回滚）----
replace_db() {
    local db_path="$1" service="$2" port="$3" new_file="$4"
    local bak="$db_path.bak-$(date +%Y%m%d)"
    log "  🔄 原子替换: $service ($db_path)"
    # 1. 备份旧库
    if [ -f "$db_path" ]; then
        cp "$db_path" "$bak"
        log "  ✅ 旧库已备份: $bak"
    fi
    # 2. 停服务（DuckDB 多进程写不支持，必须先停，且等进程退出）
    log "  🔄 停止服务 $service..."
    stop_service "$service"
    # 3. 替换
    mv -f "$new_file" "$db_path"
    log "  ✅ 新库已就位: $db_path"
    # 4. 启服务
    log "  🔄 启动服务 $service..."
    systemctl start "$service"
    # 5. 健康检查（最长 5 分钟）
    if wait_healthy "$port" "$service"; then
        return 0
    fi
    # 6. 回滚
    log "  ⚠️ 健康检查失败，回滚旧库..."
    notify_feishu "替换失败已回滚" "$service 健康检查失败, 回滚 ${db_path}"
    stop_service "$service"
    mv -f "$bak" "$db_path"
    log "  🔄 重启回滚后的服务..."
    systemctl start "$service"
    if wait_healthy "$port" "$service"; then
        log "  ✅ 已回滚到旧库并恢复服务"
    else
        log "  ❌ 回滚后服务仍未恢复，需人工介入"
        notify_feishu "回滚后服务未恢复" "$service 需人工介入, 日志: ${LOG_FILE}"
    fi
    return 1
}

# ============================================================
# 测试模式：仅构建+替换测试环境（验证用）
# ============================================================
if [ "$MODE" = "test" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] =========================" >> "$LOG_FILE"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [v3-test] 测试环境构建替换验证..." >> "$LOG_FILE"
    if ! build_and_verify /root/All_data_ch_full_test.parquet \
        /tmp/star-query-build-test.duckdb /tmp/star-query-build-test-report.json; then
        log "  ❌ [v3-test] 构建失败，结束（测试库未动）"
        exit 1
    fi
    if ! replace_db /tmp/star-query-test.duckdb star-query-test 8002 /tmp/star-query-build-test.duckdb; then
        log "  ❌ [v3-test] 替换失败，已回滚"
        exit 1
    fi
    log "  ✅ [v3-test] 测试环境构建替换验证通过"
    exit 0
fi

# ============================================================
# 正式模式：完整链路（cron 6:00）
# ============================================================
echo "[$(date '+%Y-%m-%d %H:%M:%S')] =========================" >> "$LOG_FILE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] [v3-prod] 开始增量同步..." >> "$LOG_FILE"

# 加载 .env（凭证集中管理）
set -a; source "$STAR_QUERY_DIR/.env"; set +a

# ---- Step 1: 从 ClickHouse 拉取昨日增量数据 ----
cd "$STAR_QUERY_DIR"
if ! $PYTHON clickhouse-sync.py --mode daily >> "$LOG_FILE" 2>&1; then
    log "  ❌ 增量拉取失败"
    notify_feishu "增量拉取失败" "clickhouse-sync.py 异常, 日志: ${LOG_FILE}"
    exit 1
fi

# ---- Step 2: 合并增量到全量 parquet ----
YESTERDAY=$(date -d 'yesterday' '+%Y-%m-%d')
FULL_FILE="/root/All_data_ch_full.parquet"
INCR_FILE="$STAR_QUERY_DIR/data/增量_${YESTERDAY}.parquet"
MERGED_FILE="/root/All_data_ch_full_merged.parquet"

# ---- Step 1.5: 增量 parquet 补齐彩蛋字段 ----
log "  📋 增量 parquet 补齐彩蛋字段..."
if [ -f "$INCR_FILE" ]; then
    $PYTHON "$STAR_QUERY_DIR/scripts/backfill_egg_incremental.py" "$INCR_FILE" >> "$LOG_FILE" 2>&1
    log "  ✅ 增量彩蛋补齐完成"
else
    log "  ⚠️ 增量文件不存在: $INCR_FILE"
fi

# ---- Step 1.2: 增量 parquet schema 校验（防 7/24 列不匹配复发）
# 注：必须在彩蛋补齐之后校验（原始增量 51 列，补齐后才 54 列）
log "  📋 校验增量 parquet schema..."
if ! $PYTHON "$STAR_QUERY_DIR/scripts/check_incr_schema.py" "$INCR_FILE" "$SCHEMA_SNAPSHOT"; then
    log "  ❌ 增量 schema 校验失败，中止本次同步"
    notify_feishu "增量 schema 校验失败" "文件: ${INCR_FILE}, 已中止本次同步"
    exit 1
fi
log "  ✅ 增量 schema 校验通过"

log "  📋 合并全量 parquet..."
$PYTHON -c "
import pandas as pd
import os

full = pd.read_parquet('$FULL_FILE')
incr = pd.read_parquet('$INCR_FILE')
print(f'  全量: {len(full):,} 行 | 增量: {len(incr):,} 行')

incr_ydates = incr['ydate'].unique()
print(f'  增量日期范围: {sorted(incr_ydates)}')

full = full[~full['ydate'].isin(incr_ydates)]
merged = pd.concat([full, incr], ignore_index=True)
print(f'  合并后: {len(merged):,} 行')
print(f'  日期: {merged[\"ydate\"].min()} ~ {merged[\"ydate\"].max()}')

merged.to_parquet('$MERGED_FILE', index=False)
print(f'  文件大小: {os.path.getsize(\"$MERGED_FILE\")/1024/1024:.1f} MB')
" >> "$LOG_FILE" 2>&1

mv "$MERGED_FILE" "$FULL_FILE"
log "  ✅ 已合并增量数据到全量文件"

# ---- Step 2.5: 清理过期增量 parquet（保留最近 N 天）----
log "  📋 清理 ${CLEAN_KEEP_DAYS} 天前的增量 parquet..."
CLEANED_LIST=$(find "$STAR_QUERY_DIR/data" -maxdepth 1 -name "增量_*.parquet" -mtime +${CLEAN_KEEP_DAYS} 2>/dev/null || true)
if [ -n "$CLEANED_LIST" ]; then
    CLEANED_CNT=$(echo "$CLEANED_LIST" | wc -l)
    echo "$CLEANED_LIST" | xargs -r rm -f
    log "  ✅ 已清理 ${CLEANED_CNT} 个过期增量文件"
else
    log "  ℹ️ 无过期增量文件"
fi

# ---- Step 3: 离线构建新库（build_db.py，独立进程不碰线上文件）----
BUILD_DB="/tmp/star-query-build.duckdb"
BUILD_REPORT="/tmp/star-query-build-report.json"
rm -f "$BUILD_DB" "$BUILD_REPORT"
if ! build_and_verify "$FULL_FILE" "$BUILD_DB" "$BUILD_REPORT"; then
    log "  ❌ 构建失败，保留旧库，跳过本次更新"
    exit 1
fi

# ---- Step 3.5: 为测试环境复制构建产物（正式替换 mv 会移走原文件，必须先复制）----
cp "$BUILD_DB" /tmp/star-query-build-test.duckdb
log "  ✅ 测试环境构建产物已复制"

# ---- Step 4: 原子替换正式库 ----
if ! replace_db /tmp/star-query.duckdb star-query-prod 8000 "$BUILD_DB"; then
    log "  ❌ 正式环境替换失败（已回滚），需人工处理"
    exit 1
fi

# ---- Step 5: 同步测试环境（复用同一构建产物）----
log "  📋 同步全量文件到测试环境..."
cp "$FULL_FILE" /root/All_data_ch_full_test.parquet
log "  ✅ 测试环境全量文件已更新"

if ! replace_db /tmp/star-query-test.duckdb star-query-test 8002 /tmp/star-query-build-test.duckdb; then
    log "  ⚠️ 测试环境替换失败（已回滚），需人工处理"
fi

# ---- Step 6: 药品映射表增量更新 ----
log "  📋 开始药品映射表增量更新..."
INCR_SCRIPT="/tmp/star-mapping/scripts/run_incremental.py"
if [ -f "$INCR_SCRIPT" ] && [ -f "$MAPPING_FILE" ]; then
    $PYTHON "$INCR_SCRIPT" --new-parquet "$FULL_FILE" --mapping "$MAPPING_FILE" >> "$LOG_FILE" 2>&1
    log "  ✅ 映射表增量更新完成"
else
    log "  ⚠️ 增量脚本或映射表文件不存在，跳过增量更新"
fi

log "  ✅ [v3-prod] 同步完成"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 同步完成 ✅" >> "$LOG_FILE"
