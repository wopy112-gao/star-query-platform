#!/bin/bash
# ============================================================
# 星宝数据查询系统 — 安全重启脚本
# Usage:
#   ./safe-restart.sh               # 重启生产环境（默认 8000）
#   ./safe-restart.sh --prod        # 重启生产环境
#   ./safe-restart.sh --test        # 重启测试环境（8002）
#   ./safe-restart.sh --prod --regtest  # 重启 + 回归测试
# ============================================================

set -euo pipefail

# ---- 颜色 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# ---- 版本 ----
SCRIPT_VERSION="1.0.0"

# ---- 配置 ----
PROD_PORT=8000
TEST_PORT=8002
PROD_DIR="/root/.lightclaw/workspace/star-query"
TEST_DIR="/root/.lightclaw/workspace/star-query-test"
HEALTH_TIMEOUT=30  # 健康检查最长等待（秒）
HEALTH_INTERVAL=3  # 每次轮询间隔（秒）

# ---- 检测环境 ----
ENV="生产"
PORT=$PROD_PORT
DIR=$PROD_DIR
LOG_FILE="/tmp/star-query-production.log"

if [ "${1:-}" = "--test" ]; then
    ENV="测试"
    PORT=$TEST_PORT
    DIR=$TEST_DIR
    LOG_FILE="/tmp/star-query-test.log"
elif [ "${1:-}" = "--prod" ] || [ "${1:-}" = "" ]; then
    : # 默认生产
else
    echo -e "${RED}用法: $0 [--prod | --test]${NC}"
    exit 1
fi

echo ""
echo -e "${CYAN}══════════════════════════════════════════${NC}"
echo -e "${CYAN}  星宝数据查询系统 — 安全重启 ${BOLD}$ENV${NC}${CYAN}环境${NC}"
echo -e "${CYAN}══════════════════════════════════════════${NC}"
echo ""

# ---- Step 1: 检查目录 ----
if [ ! -d "$DIR" ]; then
    echo -e "${RED}[✗] 错误: 项目目录不存在 → $DIR${NC}"
    exit 1
fi

cd "$DIR/backend" 2>/dev/null || {
    echo -e "${RED}[✗] 错误: 后端目录不存在 → $DIR/backend${NC}"
    exit 1
}

# ---- Step 2: 确认旧进程并优雅停止 ----
echo -e "${YELLOW}[1/5] 检测端口 $PORT 上的旧进程...${NC}"

OLD_PIDS=$(fuser "$PORT/tcp" 2>/dev/null || true)
if [ -n "$OLD_PIDS" ]; then
    OLD_COUNT=$(echo "$OLD_PIDS" | wc -w)
    echo -e "     发现 ${BOLD}$OLD_COUNT${NC} 个进程占用端口 $PORT，正在停止..."
    echo -e "     PID(s): $OLD_PIDS"

    # 先发 SIGTERM（优雅关闭）
    fuser -k -TERM "$PORT/tcp" 2>/dev/null || true
    sleep 2

    # 检查是否已释放
    if fuser "$PORT/tcp" 2>/dev/null > /dev/null; then
        echo -e "     ${YELLOW}进程未响应 SIGTERM，发送 SIGKILL...${NC}"
        fuser -k -KILL "$PORT/tcp" 2>/dev/null || true
        sleep 1
    fi

    echo -e "     ${GREEN}✓ 旧进程已终止${NC}"
else
    echo -e "     ${GREEN}✓ 端口 $PORT 空闲，无需清理${NC}"
fi

# ---- 补杀 reloader 残留进程 ----
# uvicorn reload=True 会产生 reloader 父进程，fuser 可能只杀 worker
# 精准定位：只杀正在监听当前端口的 Python 进程
LOCAL_PIDS=$(fuser "$PORT/tcp" 2>/dev/null || true)
if [ -z "$LOCAL_PIDS" ]; then
    # fuser 没找到，尝试通过 ss 定位
    LOCAL_PIDS=$(ss -tlnp sport = :$PORT 2>/dev/null | grep -oP 'pid=\K\d+' | tr '\n' ' ' || true)
fi
if [ -n "$LOCAL_PIDS" ]; then
    echo -e "     补杀端口 $PORT 上的残余进程 (PID: $LOCAL_PIDS)..."
    for pid in $LOCAL_PIDS; do
        kill -TERM "$pid" 2>/dev/null || true
    done
    sleep 2
fi

# 等待端口彻底释放（最多 10 秒）
echo -e "     等待端口释放..."
for i in $(seq 10); do
    if ! fuser "$PORT/tcp" 2>/dev/null > /dev/null; then
        echo -e "     ${GREEN}✓ 端口已释放${NC}"
        break
    fi
    if [ "$i" -eq 10 ]; then
        echo -e "${RED}[✗] 端口 $PORT 超过 10 秒仍未释放，请手动检查: lsof -i :$PORT${NC}"
        exit 1
    fi
    sleep 1
done

echo ""

# ---- Step 3: 检查环境配置 ----
echo -e "${YELLOW}[2/5] 检查后端依赖...${NC}"

# 检查 .env
if [ -f "$DIR/.env" ]; then
    # 至少确认 .env 有内容
    ENV_LINES=$(grep -c . "$DIR/.env" 2>/dev/null || echo 0)
    echo -e "     ✓ .env 配置文件 ($ENV_LINES 行)"
else
    echo -e "     ${YELLOW}⚠ .env 不存在，将使用 config.py 默认值${NC}"
fi

# 检查 app.py
if [ -f "app.py" ]; then
    echo -e "     ✓ app.py 就绪"
else
    echo -e "${RED}[✗] 缺少 app.py${NC}"
    exit 1
fi

echo ""

# ---- 设置 systemd 服务名 ----
SYSTEMD_SERVICE="star-query-prod"
if [ "${1:-}" = "--test" ]; then
    SYSTEMD_SERVICE="star-query-test"
fi

SYSTEMD_AVAILABLE=false
if systemctl list-unit-files "$SYSTEMD_SERVICE.service" &>/dev/null 2>&1; then
    SYSTEMD_AVAILABLE=true
fi

# ---- Step 4: 启动服务 ----
echo -e "${YELLOW}[3/5] 启动服务...${NC}"

if $SYSTEMD_AVAILABLE; then
    echo -e "     方式: ${BOLD}systemd${NC} (${BOLD}$SYSTEMD_SERVICE${NC})"
    systemctl restart "$SYSTEMD_SERVICE"
    sleep 2

    if ! systemctl is-active --quiet "$SYSTEMD_SERVICE"; then
        echo -e "${RED}[✗] systemd 启动失败，最近日志:${NC}"
        journalctl -u "$SYSTEMD_SERVICE" --no-pager -n 15 2>/dev/null | tail -10
        exit 1
    fi

    NEW_PID=$(systemctl show -p MainPID "$SYSTEMD_SERVICE" --value 2>/dev/null || echo "?")
    echo -e "     PID: ${BOLD}$NEW_PID${NC}"
    echo -e "     ${GREEN}✓ systemd 服务已启动${NC}"
else
    # Fallback: 无 systemd 时使用 nohup
    echo -e "     方式: ${BOLD}nohup${NC} (systemd 服务不存在)"
    echo -e "     端口: ${BOLD}$PORT${NC}"
    echo -e "     日志: ${BOLD}$LOG_FILE${NC}"

    nohup python3 app.py > "$LOG_FILE" 2>&1 &
    NEW_PID=$!
    echo -e "     PID: ${BOLD}$NEW_PID${NC}"
    sleep 2

    if ! kill -0 "$NEW_PID" 2>/dev/null; then
        echo -e "${RED}[✗] 进程启动失败，最近日志:${NC}"
        tail -5 "$LOG_FILE" 2>/dev/null || true
        exit 1
    fi
    echo -e "     ${GREEN}✓ 进程存活${NC}"
fi
echo ""

# ---- Step 5: 健康检查 ----
echo -e "${YELLOW}[4/5] 健康检查...${NC}"

HEALTH_URL="http://localhost:$PORT/api/health"
START_TIME=$(date +%s)
HEALTHY=false

while true; do
    NOW=$(date +%s)
    ELAPSED=$((NOW - START_TIME))
    if [ "$ELAPSED" -ge "$HEALTH_TIMEOUT" ]; then
        echo -e "     ${RED}[✗] 健康检查超时（${HEALTH_TIMEOUT}s）${NC}"
        if $SYSTEMD_AVAILABLE; then
            journalctl -u "$SYSTEMD_SERVICE" --no-pager -n 10 2>/dev/null | tail -10
        else
            tail -10 "$LOG_FILE" 2>/dev/null || true
        fi
        exit 1
    fi

    RESP=$(curl -s --connect-timeout 5 --max-time 10 -o /dev/null -w "%{http_code}" "$HEALTH_URL" 2>/dev/null || echo "000")

    if [ "$RESP" = "200" ]; then
        HEALTHY=true
        break
    fi

    sleep "$HEALTH_INTERVAL"
done

# 获取健康详情
HEALTH_INFO=$(curl -s --connect-timeout 5 --max-time 10 "$HEALTH_URL" 2>/dev/null || echo '{"status":"unknown"}')

echo -e "     ✓ HTTP 200 OK"
echo -e "     详情: $HEALTH_INFO"
echo -e "     ${GREEN}✓ 服务正常运行${NC}"
echo ""

# ---- 完成 ----
echo -e "${GREEN}══════════════════════════════════════════${NC}"
echo -e "${GREEN}  ${BOLD}✔ 安全重启完成！${NC}${GREEN}"
echo -e "${GREEN}  环境: ${BOLD}$ENV${NC}"
echo -e "${GREEN}  端口: ${BOLD}$PORT${NC}"
echo -e "${GREEN}  PID:  ${BOLD}$NEW_PID${NC}"
echo -e "${GREEN}  时间: ${BOLD}$(date '+%Y-%m-%d %H:%M:%S')${NC}"
echo -e "${GREEN}  健康: ${BOLD}$(echo "$HEALTH_INFO" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"数据加载: {'✓' if d.get('data_loaded') else '✗'} | 行数: {d.get('total_rows','?')}\")" 2>/dev/null || echo "OK")${NC}"
echo -e "${GREEN}══════════════════════════════════════════${NC}"
echo ""
echo -e "  访问地址: ${CYAN}http://$(curl -s ifconfig.me 2>/dev/null || echo 'localhost'):$PORT${NC}"
echo ""

# Step 6: 回归测试（可选）
if [ "${2:-}" = "--regtest" ] || [ "${1:-}" = "--regtest" ]; then
    echo ""
    echo -e "${YELLOW}[6/6] 回归测试...${NC}"
    REGTEST=""
    if [ "$ENV" = "测试" ]; then
        REGTEST=$(python3 "$DIR/backend/regression_test.py" 2>&1)
    else
        REGTEST=$(python3 "$DIR/backend/regression_test.py" --prod 2>&1)
    fi
    echo "$REGTEST" | tail -5
    FAILED=$(echo "$REGTEST" | grep -c "^  \[.*\] ❌")
    if [ "$FAILED" -gt 0 ]; then
        echo -e "     ${RED}[✗] $FAILED 个用例失败，详见 regression_reports/${NC}"
    else
        echo -e "     ${GREEN}✓ 全部通过${NC}"
    fi
fi
echo ""
