#!/bin/bash
# ============================================================
# 星宝数据平台 — 全量数据修复脚本（入口）
# 用法: bash scripts/restore_full_data.sh
# ============================================================
set -e

cd "$(dirname "$0")/.."
PYTHON=/root/.lightclaw/venv/bin/python3

echo "=========================================="
echo " 星宝数据平台 — 全量数据修复"
echo "=========================================="
echo ""

$PYTHON scripts/restore_full_data.py
