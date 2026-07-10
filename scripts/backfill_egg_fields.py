#!/usr/bin/env python3
"""
backfill_egg_fields.py — 为星宝全量 parquet 补齐5个彩蛋字段

数据来源：
  1. /root/All_data_ch_full.parquet — 当前全量主数据
  2. 彩蛋 xlsx（由 upload 路径传入）— 彩蛋任务数据

输出：
  写出新 parquet，在原有列基础上增加5个彩蛋列：
  - 彩蛋任务ID
  - 彩蛋药品名称
  - 彩蛋标题
  - 是否分子1=是(发分)
  - 命中原因
"""

import os
import sys
import time
import pandas as pd

# ============================================================
# 路径配置
# ============================================================

PARQUET_PATH = "/root/All_data_ch_full.parquet"
XLSX_PATH = os.path.join(
    os.path.dirname(__file__),
    "..", "..", "uploads",
    "66247e28998a4acab29ae45e51283127.xlsx",
)
XLSX_PATH = os.path.normpath(os.path.abspath(XLSX_PATH))

# 如果 uploads 路径不存在，尝试在 star-query 目录下找
if not os.path.exists(XLSX_PATH):
    XLSX_PATH = os.path.join(
        os.path.dirname(__file__),
        "..", "..", "..",
        "uploads", "66247e28998a4acab29ae45e51283127.xlsx",
    )
    XLSX_PATH = os.path.normpath(os.path.abspath(XLSX_PATH))

BACKUP_DIR = os.path.join(os.path.dirname(PARQUET_PATH), "backup")
OUTPUT_PATH = PARQUET_PATH  # 直接覆盖原文件，先写临时文件再 rename


def main():
    t0 = time.time()
    print(f"┌────────────────────────────────────────────┐")
    print(f"│  星宝全量 parquet 彩蛋字段补齐              │")
    print(f"└────────────────────────────────────────────┘")
    print()

    # ---- Step 1: 读 xlsx 彩蛋数据 ----
    print("📖 读取彩蛋 xlsx...")
    if not os.path.exists(XLSX_PATH):
        print(f"  ❌ 文件不存在: {XLSX_PATH}")
        sys.exit(1)

    egg_raw = pd.read_excel(XLSX_PATH, sheet_name="Result 1")
    print(f"  xlsx 原始行数: {len(egg_raw):,}")

    # 按场景ID去重（同场景ID彩蛋数据相同）
    egg_map = egg_raw.drop_duplicates(subset=["cc.场景ID"]).copy()
    egg_map.rename(columns={
        "cc.场景ID": "场景ID",
        "彩蛋任务ID": "彩蛋任务ID",
        "彩蛋药品名称": "彩蛋药品名称",
        "彩蛋标题": "彩蛋标题",
        "是否分子1=是(发分)": "是否分子1=是(发分)",
        "命中原因": "命中原因",
    }, inplace=True)
    egg_map = egg_map[["场景ID", "彩蛋任务ID", "彩蛋药品名称", "彩蛋标题",
                        "是否分子1=是(发分)", "命中原因"]]
    print(f"  去重后: {len(egg_map):,} 条唯一场景")
    print()

    # ---- Step 2: 读全量 parquet ----
    print("📖 读取全量 parquet...")
    t1 = time.time()
    df = pd.read_parquet(PARQUET_PATH)
    elapsed_read = time.time() - t1
    print(f"  总行数: {len(df):,}")
    print(f"  列数: {len(df.columns)}")
    print(f"  读取耗时: {elapsed_read:.1f}秒")
    print()

    # 检查是否已有彩蛋字段（避免重复执行）
    existing_egg_cols = [c for c in df.columns if c in egg_map.columns[1:]]
    if existing_egg_cols:
        print(f"  ⚠️ 已有彩蛋字段: {existing_egg_cols}，跳过已有列")
        egg_map = egg_map.drop(columns=[c for c in existing_egg_cols if c != "场景ID"], errors="ignore")

    # ---- Step 3: left join 补齐 ----
    print("🔗 left join 补齐彩蛋字段...")
    t2 = time.time()
    # 转场景ID为相同类型
    df["场景ID"] = df["场景ID"].astype("int64")
    egg_map["场景ID"] = egg_map["场景ID"].astype("int64")

    before = len(df)
    df = df.merge(egg_map, on="场景ID", how="left")
    after = len(df)
    print(f"  merge 前: {before:,} 行, merge 后: {after:,} 行")
    assert before == after, f"merge 导致行数变化！{before} → {after}"
    print(f"  left join 耗时: {time.time() - t2:.1f}秒")
    print()

    # ---- Step 4: 统计补齐情况 ----
    egg_cols = ["彩蛋任务ID", "彩蛋药品名称", "彩蛋标题", "是否分子1=是(发分)", "命中原因"]
    for col in egg_cols:
        non_null = df[col].notna().sum()
        fill_pct = non_null / len(df) * 100
        print(f"  {col}: 已补齐 {non_null:,} 行 ({fill_pct:.2f}%)")

    # 给未匹配的行填充默认值
    df["彩蛋任务ID"] = df["彩蛋任务ID"].fillna(0).astype("int64")
    df["彩蛋药品名称"] = df["彩蛋药品名称"].fillna("")
    df["彩蛋标题"] = df["彩蛋标题"].fillna("")
    df["是否分子1=是(发分)"] = df["是否分子1=是(发分)"].fillna(0).astype("int64")
    df["命中原因"] = df["命中原因"].fillna("")

    print(f"\n  默认值填充完成，0缺失")
    print()

    # ---- Step 5: 写临时文件再替换 ----
    print("💾 写出新 parquet...")
    t3 = time.time()

    # 给原文件做备份
    os.makedirs(BACKUP_DIR, exist_ok=True)
    backup_path = os.path.join(BACKUP_DIR, f"All_data_ch_full_before_egg.{int(time.time())}.parquet")
    print(f"  📦 备份原文件 → {backup_path}")
    os.rename(PARQUET_PATH, backup_path)

    # 写新 parquet
    temp_path = PARQUET_PATH + ".tmp"
    df.to_parquet(temp_path, index=False)
    os.rename(temp_path, PARQUET_PATH)

    elapsed_write = time.time() - t3
    size_mb = os.path.getsize(PARQUET_PATH) / 1024 / 1024
    print(f"  新 parquet 大小: {size_mb:.1f} MB")
    print(f"  写入耗时: {elapsed_write:.1f}秒")
    print()

    # ---- 完成 ----
    total_elapsed = time.time() - t0
    print(f"✅ 完成！总耗时: {total_elapsed:.1f}秒")
    print(f"   原列: 原列数 + 5个彩蛋列 = {len(df.columns)} 列")
    print(f"   位置: {PARQUET_PATH}")
    print(f"   备份: {backup_path}")
    print()
    print("后续步骤:")
    print("  1. 修改 clickhouse-sync.py 增量 SQL 加入彩蛋 left join")
    print("  2. 修改 schema_knowledge.py 注册彩蛋字段")


if __name__ == "__main__":
    main()
