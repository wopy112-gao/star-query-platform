#!/bin/bash
# 一键回滚脚本 - 恢复部署前的备份文件
# 用法: bash rollback.sh 20260630_151516

set -euo pipefail

TIMESTAMP="${1:-}"
if [ -z "$TIMESTAMP" ]; then
    echo "用法: bash rollback.sh <备份时间戳>"
    echo "可用备份:"
    ls -1 /root/.lightclaw/workspace/star-query/backend/*.bak.* 2>/dev/null | head -5
    exit 1
fi

BACKEND="/root/.lightclaw/workspace/star-query/backend"

echo "=== 回滚到备份 $TIMESTAMP ==="
for f in sql_engine.py config.py app.py query_router.py data_export_router.py; do
    bak="$BACKEND/$f.bak.$TIMESTAMP"
    if [ -f "$bak" ]; then
        cp "$bak" "$BACKEND/$f"
        echo "  ✅ $f 已恢复"
    else
        echo "  ⚠️ $f 备份不存在: $bak"
    fi
done

# 恢复 .env
ENV_BAK="$BACKEND/../.env.bak.$TIMESTAMP"
if [ -f "$ENV_BAK" ]; then
    cp "$ENV_BAK" "$BACKEND/../.env"
    echo "  ✅ .env 已恢复"
fi

echo ""
echo "回滚完成！请手动执行: cd $BACKEND/.. && bash safe-restart.sh --prod"
