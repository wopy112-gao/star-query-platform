#!/usr/bin/env python3
"""
backfill_egg_incremental.py — 为增量 parquet 补齐彩蛋字段

背景：clickhouse-sync.py 拉取的增量 parquet 缺彩蛋字段，data 表是 54 列，
增量加载 INSERT 时列数不匹配导致每天失败。

v3（2026-08-10）：数据源从静态彩蛋 xlsx 改为 ClickHouse yaoxin56 库直查！
  之前依赖 uploads/66247e28...xlsx（7/9 静态快照），8 月及以后数据 3 列全空。
  已确认 CH 存在实时映射表：
    yaoxin56.x_ai_assistant_scenario_denominator  (scenario_id → egg_id + reason)
    yaoxin56.x_ai_amazing_egg                     (id → drug_name + title)
  ⚠️ 关键：分母表 scenario_id 对应增量 parquet 的「原始场景ID」，不是「场景ID」！
  按 原始场景ID left join 补齐 3 列：彩蛋药品名称、彩蛋标题、命中原因。

v2（2026-08-07 Phase 2）：彩蛋任务ID / 是否分子1=是(发分) 已由 clickhouse-sync.py
从 CH 源表直取（egg_id / egg_drug_deal），本脚本只需补齐剩余 3 个描述字段。

用法：
  python3 backfill_egg_incremental.py <增量.parquet>
  # 原地补齐（先备份 .bak），输出 54 列
"""

import os
import sys
import time
import shutil
import pandas as pd

# ---- ClickHouse 连接（与 clickhouse-egg-sync.py 一致，支持环境变量覆盖）----
CH_HOST = os.environ.get("CH_HOST", "cc-2ze4vp6kio9ns5605.public.clickhouse.ads.aliyuncs.com")
CH_PORT = int(os.environ.get("CH_PORT", "8123"))
CH_USER = os.environ.get("CH_USER", "yaoxin_ai_select")
CH_PASS = os.environ.get("CH_PASS", "4-s7D4HHcR8df3fh8kSO")

# 需补齐的 3 个描述字段（彩蛋任务ID/是否分子1=是(发分) 已由 CH 直取）
EGG_COLS = ["彩蛋药品名称", "彩蛋标题", "命中原因"]


def load_egg_map():
    """从 ClickHouse 拉取 原始场景ID → 彩蛋字段 映射（实时，覆盖 8 月及以后数据）"""
    import clickhouse_connect
    client = clickhouse_connect.get_client(
        host=CH_HOST, port=CH_PORT,
        username=CH_USER, password=CH_PASS,
    )
    rows = client.query("""
        SELECT
            sd.scenario_id                          AS `原始场景ID`,
            egg.drug_name                           AS `彩蛋药品名称`,
            egg.title                               AS `彩蛋标题`,
            sd.reason                               AS `命中原因`
        FROM yaoxin56.x_ai_assistant_scenario_denominator sd
        LEFT JOIN yaoxin56.x_ai_amazing_egg egg
            ON egg.id = sd.egg_id
        WHERE sd.egg_id > 0
    """).result_rows

    egg_map = pd.DataFrame(rows, columns=["原始场景ID"] + EGG_COLS)
    egg_map["原始场景ID"] = egg_map["原始场景ID"].astype("int64")
    # 同场景ID彩蛋数据相同，去重
    egg_map = egg_map.drop_duplicates(subset=["原始场景ID"]).copy()
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

    # ---- Step 1: 读彩蛋映射（CH 直查）----
    print("📡 从 ClickHouse 拉取彩蛋映射...")
    egg_map = load_egg_map()
    print(f"  彩蛋映射: {len(egg_map):,} 条唯一原始场景")
    print()

    # ---- Step 2: 读增量 parquet ----
    print(f"📖 读取增量 parquet: {parquet_path}")
    df = pd.read_parquet(parquet_path)
    print(f"  行数: {len(df):,} | 列数: {len(df.columns)}")

    # 已含全部补齐列且有真实值则跳过（幂等）；全空（旧 xlsx 补过但没匹配上）则重补
    existing = [c for c in EGG_COLS if c in df.columns]
    if len(existing) == len(EGG_COLS):
        has_real = df[EGG_COLS[0]].astype(str).str.strip().ne("").any()
        if has_real:
            print(f"  ⚠️ 已包含补齐列且有真实值: {existing}，跳过")
            return

    assert "原始场景ID" in df.columns, f"增量 parquet 缺少 原始场景ID 列! 实际列: {list(df.columns)}"
    print()

    # ---- Step 3: 删掉全空的旧补齐列（避免 merge 冲突），再 left join ----
    for col in EGG_COLS:
        if col in df.columns:
            df = df.drop(columns=[col])
            print(f"  🧹 删除全空的旧列: {col}")

    print("🔗 left join 补齐彩蛋字段（按 原始场景ID）...")
    df["原始场景ID"] = df["原始场景ID"].astype("int64")

    before = len(df)
    df = df.merge(egg_map, on="原始场景ID", how="left")
    assert before == len(df), f"merge 导致行数变化！{before} → {len(df)}"

    # ---- Step 4: 统计补齐情况 ----
    for col in EGG_COLS:
        non_empty = df[col].notna() & df[col].astype(str).str.strip().ne("")
        print(f"  {col}: 补齐 {non_empty.sum():,} 行 ({non_empty.sum()/len(df)*100:.2f}%)")

    # 默认值填充（补齐的列）
    for col in EGG_COLS:
        df[col] = df[col].fillna("")
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
