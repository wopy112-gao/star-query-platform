#!/bin/bash
# 星宝数据平台安全备份脚本（单向：只 push 不 pull）
# 如果远程有变更导致 push 失败，告警而非自动合并
set -e

DIR="/root/.lightclaw/workspace/star-query"
LOG="/tmp/star-query-backup.log"

cd "$DIR"

# Step 1: commit 本地变更
git add -A
if git diff --cached --quiet; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 无变更，跳过" >> "$LOG"
    exit 0
fi
git commit -m "daily backup: $(date +%Y-%m-%d)"

# Step 2: 尝试 push（不会自动 pull）
if git push origin master 2>&1; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ 备份成功" >> "$LOG"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⚠️ 备份失败：远程有冲突，请手动处理" >> "$LOG"
    # push 失败时回退本地 commit，避免本地和远程不一致
    git reset --soft HEAD~1
    exit 1
fi
