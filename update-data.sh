#!/bin/bash
# ============================================================
# 星宝语料数据更新工具
# 用途：上传新 xlsx 数据后，一键转 parquet + 更新配置 + 重启
# 用法：sudo bash update-data.sh /path/to/新数据.xlsx
# ============================================================

set -e

# ---- 配置 ----
NEW_FILE="$1"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
BACKEND_DIR="$SCRIPT_DIR/backend"
SCHEMA_FILE="$BACKEND_DIR/schema_knowledge.py"

# ---- 颜色 ----
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}╔══════════════════════════════════════╗${NC}"
echo -e "${BLUE}║    星宝语料数据更新工具              ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════╝${NC}"
echo ""

# ---- 检查参数 ----
if [ -z "$NEW_FILE" ]; then
    echo -e "${YELLOW}用法:${NC} bash update-data.sh /path/to/新数据.xlsx"
    echo "示例: bash update-data.sh /root/All_data20260601.xlsx"
    exit 1
fi

if [ ! -f "$NEW_FILE" ]; then
    echo -e "${YELLOW}错误:${NC} 文件不存在: $NEW_FILE"
    exit 1
fi

# ---- 检查文件扩展名 ----
EXT="${NEW_FILE##*.}"
if [ "$EXT" != "xlsx" ] && [ "$EXT" != "csv" ]; then
    echo -e "${YELLOW}错误:${NC} 仅支持 .xlsx 或 .csv 格式"
    exit 1
fi

PYTHON=/root/.lightclaw/venv/bin/python3

echo -e "${GREEN}[1/4]${NC} 读取数据文件..."
$PYTHON -c "
import pandas as pd
df = pd.read_excel('$NEW_FILE')
print(f'  → {len(df):,} 行 × {len(df.columns)} 列')
print(f'  → 列名: {list(df.columns)}')
" 2>/dev/null || $PYTHON -c "
import pandas as pd
df = pd.read_csv('$NEW_FILE')
print(f'  → {len(df):,} 行 × {len(df.columns)} 列')
print(f'  → 列名: {list(df.columns)}')
"

# ---- 转 parquet ----
PARQUET_FILE="${NEW_FILE%.*}.parquet"
echo ""
echo -e "${GREEN}[2/4]${NC} 转换 xlsx → parquet（加速加载）..."
$PYTHON -c "
import pandas as pd
import time
start = time.time()
if '$EXT' == 'csv':
    df = pd.read_csv('$NEW_FILE')
else:
    df = pd.read_excel('$NEW_FILE')
df.to_parquet('$PARQUET_FILE', index=False)
elapsed = time.time() - start
print(f'  ✅ {len(df):,} 行 → {PARQUET_FILE}')
print(f'  ⏱  耗时: {elapsed:.1f}秒')
"

# ---- 获取行数 ----
TOTAL_ROWS=$($PYTHON -c "import pandas as pd; print(len(pd.read_parquet('$PARQUET_FILE')))")

# ---- 更新 .env ----
echo ""
echo -e "${GREEN}[3/4]${NC} 更新配置..."
sed -i "s|DATA_PATH=.*|DATA_PATH=$PARQUET_FILE|" "$ENV_FILE"
echo "  ✅ .env → DATA_PATH=$PARQUET_FILE"

# ---- 更新 schema_knowledge.py ----
sed -i "s|\"total_rows\": [0-9]*|\"total_rows\": $TOTAL_ROWS|" "$SCHEMA_FILE"
sed -i "s|\"source\": \".*\"|\"source\": \"$PARQUET_FILE\"|" "$SCHEMA_FILE"
echo "  ✅ schema_knowledge.py → total_rows=$TOTAL_ROWS"

# ---- 重启后端 ----
echo ""
echo -e "${GREEN}[4/4]${NC} 重启后端服务..."
kill $(lsof -ti:8000 2>/dev/null) 2>/dev/null || true
sleep 1

cd "$BACKEND_DIR"
nohup $PYTHON -m uvicorn app:app --host 0.0.0.0 --port 8000 --log-level error > /tmp/starquery-8000.log 2>&1 &

echo ""
echo -e "${GREEN}╔══════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  更新完成！后端正在启动...            ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════╝${NC}"
echo ""
echo -e "  数据: ${BLUE}$PARQUET_FILE${NC}"
echo -e "  行数: ${BLUE}$TOTAL_ROWS${NC}"
echo -e "  端口: ${BLUE}8000${NC}"
echo ""
echo -e "  ${YELLOW}查看启动状态:${NC}"
echo -e "  tail -f /tmp/starquery-8000.log"
echo -e ""
echo -e "  ${YELLOW}健康检查:${NC}"
echo -e "  curl http://localhost:8000/api/health"
echo ""
