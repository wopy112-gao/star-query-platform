#!/bin/bash
# 本地独立备份（与 GitHub 解耦），保留最近 3 天
set -e

BACKUP_DIR="/root/.lightclaw/workspace/backups"
WS="/root/.lightclaw/workspace"
RETENTION_DAYS=3
TIMESTAMP=$(date +%Y%m%d)

mkdir -p "$BACKUP_DIR"

# 备份正式环境代码（排除 .env、data、*.db 等运行时数据）
tar czf "$BACKUP_DIR/star-query-prod-$TIMESTAMP.tar.gz" \
  -C "$WS" \
  --exclude='.env' \
  --exclude='data' \
  --exclude='*.db' \
  --exclude='node_modules' \
  --exclude='__pycache__' \
  --exclude='*.parquet' \
  star-query/backend \
  star-query/frontend \
  star-query/safe-restart.sh \
  star-query/update-data.sh \
  star-query/RELEASE_NOTES.md \
  star-query/config.py

echo "[$(date)] ✅ 本地备份: star-query-prod-$TIMESTAMP.tar.gz"

# 清理超过 RETENTION_DAYS 天的旧备份
find "$BACKUP_DIR" -name "star-query-*.tar.gz" -mtime +$RETENTION_DAYS -delete
ls -lh "$BACKUP_DIR/"star-query-*.tar.gz 2>/dev/null
