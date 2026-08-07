#!/usr/bin/env python3
"""
backfill_egg_incremental.py — 为增量 parquet 补齐彩蛋字段

背景：clickhouse-sync.py 拉取的增量 parquet 缺彩蛋字段，data 表是 54 列，
增量加载 INSERT 时列数不匹配导致每天失败。

v2（2026-08-07 Phase 2）：彩蛋任务ID / 是否分子1=是(发分) 已由 clickhouse-sync.py
从 CH 源表直取（egg_id / egg_drug_deal），本脚本只需补齐剩余 3 个描述字段：
彩蛋药品名称、彩蛋标题、命中原因（数据源：彩蛋 xlsx，按场景ID left join）。

用法：
  python3 backfill_egg_incremental.py <增量.parquet>
  # 原地补齐（先备份 .bak），输出 54 列
"""

import os
import sys
import time
import shutil
import pandas as pd

# 彩蛋 xlsx 路径（与 backfill_egg_fields.py 一致）
XLSX_PATH = "/root/.lightclaw/workspace/uploads/66247e28998a4acab29ae45e51283127.xlsx"

# 只需 xlsx 补齐的列（其余2列：彩蛋任务ID/是否分子1=是(发分) 已由 CH 直取）
XLSX_EGG_COLS = ["彩蛋药品名称", "彩蛋标题", "命中原因"]


def load_egg_map():
    """读取彩蛋 xlsx，返回 场景ID → 彩蛋字段 的映射 DataFrame（仅取需要补齐的列）"""
    if not os.path.exists(XLSX_PATH):
        raise FileNotFoundError(f"彩蛋 xlsx 不存在: {XLSX_PATH}")

    egg_raw = pd.read_excel(XLSX_PATH, sheet_name="Result 1")
    # 按场景ID去重（同场景ID彩蛋数据相同）
    egg_map = egg_raw.drop_duplicates(subset=["cc.场景ID"]).copy()
    egg_map.rename(columns={"cc.场景ID": "场景ID"}, inplace=True)
    egg_map = egg_map[["场景ID"] + XLSX_EGG_COLS]
    return egg_map


def main():
    t0 = time.time()
    if len(sys.argv) < 2:
        print("用法: python3 backfill_egg_incremental.py <增量.parquet>")
        sys.exit(1)

    parquet_path = sys.argv[1]
    print(f"┌────────────────────────────────────────────┐")
    print(f"│  增量 parquet 彩蛋字段补齐                  │")
    print(f"└────────────────────────────────────────────┘")
    print()

    # ---- Step 1: 读彩蛋映射 ----
    print("📖 读取彩蛋 xlsx...")
    egg_map = load_egg_map()
    print(f"  彩蛋映射: {len(egg_map):,} 条唯一场景")
    print()

    # ---- Step 2: 读增量 parquet ----
    print(f"📖 读取增量 parquet: {parquet_path}")
    df = pd.read_parquet(parquet_path)
    print(f"  行数: {len(df):,} | 列数: {len(df.columns)}")

    # 已含全部 xlsx 补齐列则跳过（幂等）
    existing = [c for c in XLSX_EGG_COLS if c in df.columns]
    if len(existing) == len(XLSX_EGG_COLS):
        print(f"  ⚠️ 已包含 xlsx 补齐列: {existing}，跳过")
        return

    assert "场景ID" in df.columns, f"增量 parquet 缺少 场景ID 列! 实际列: {list(df.columns)}"
    print()

    # ---- Step 3: left join 补齐 ----
    print("🔗 left join 补齐彩蛋字段...")
    df["场景ID"] = df["场景ID"].astype("int64")
    egg_map["场景ID"] = egg_map["场景ID"].astype("int64")

    before = len(df)
    df = df.merge(egg_map, on="场景ID", how="left")
    assert before == len(df), f"merge 导致行数变化！{before} → {len(df)}"

    # ---- Step 4: 统计补齐情况 ----
    for col in XLSX_EGG_COLS:
        non_null = df[col].notna().sum()
        print(f"  {col}: 补齐 {non_null:,} 行 ({non_null/len(df)*100:.2f}%)")

    # 默认值填充（xlsx 补齐的列）
    df["彩蛋药品名称"] = df["彩蛋药品名称"].fillna("")
    df["彩蛋标题"] = df["彩蛋标题"].fillna("")
    df["命中原因"] = df["命中原因"].fillna("")
    print("  默认值填充完成")

    # ---- Step 5: 备份原文件 + 写回 ----
    print("💾 写出...")
    backup_path = parquet_path + ".bak"
    if not os.path.exists(backup_path):
        shutil.copy2(parquet_path, backup_path)
        print(f"  📦 备份 → {backup_path}")

    temp_path = parquet_path + ".tmp"
    df.to_parquet(temp_path, index=False)
    os.rename(temp_path, parquet_path)

    size_mb = os.path.getsize(parquet_path) / 1024 / 1024
    print(f"  ✅ 完成: {len(df):,} 行 × {len(df.columns)} 列 ({size_mb:.1f} MB)")
    print(f"  耗时: {time.time()-t0:.1f}秒")


if __name__ == "__main__":
    main()
