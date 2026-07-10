#!/usr/bin/env python3
"""
clickhouse-egg-sync.py — 星宝彩蛋数据独立同步

从 ClickHouse yaoxin56 库拉取彩蛋两份表，合并为场景→彩蛋映射 parquet。
输出路径: star-query/data/egg_mapping.parquet

由 DuckDB 加载时自动读取并 left join。
"""

import os
import time
from datetime import date

import clickhouse_connect
import pandas as pd

CH_HOST = "cc-2ze4vp6kio9ns5605.public.clickhouse.ads.aliyuncs.com"
CH_PORT = 8123
CH_USER = "yaoxin_ai_select"
CH_PASS = "4-s7D4HHcR8df3fh8kSO"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(SCRIPT_DIR, "data", "egg_mapping.parquet")

SYNC_SQL = """
SELECT
    sd.scenario_id                                    AS `场景ID`,
    sd.egg_id                                         AS `彩蛋任务ID`,
    egg.drug_name                                     AS `彩蛋药品名称`,
    egg.title                                         AS `彩蛋标题`,
    sd.is_numerator                                   AS `是否分子1=是(发分)`,
    sd.reason                                         AS `命中原因`
FROM yaoxin56.x_ai_assistant_scenario_denominator sd
LEFT JOIN yaoxin56.x_ai_amazing_egg egg
    ON egg.id = sd.egg_id
WHERE sd.egg_id > 0
"""


def get_client():
    return clickhouse_connect.get_client(
        host=CH_HOST, port=CH_PORT,
        username=CH_USER, password=CH_PASS,
    )


def main():
    t0 = time.time()
    print(f"┌────────────────────────────────────────────┐")
    print(f"│  星宝彩蛋数据同步                           │")
    print(f"└────────────────────────────────────────────┘")
    print()

    print("📡 连接 ClickHouse...")
    client = get_client()
    print(f"  ✅ 连接成功 (v{client.server_version})")
    print()

    print("📥 拉取彩蛋映射数据...")
    result = client.query(SYNC_SQL)
    elapsed = time.time() - t0
    print(f"  ⏱  查询耗时: {elapsed:.1f}秒")
    print(f"  📦 行数: {result.row_count:,}")

    df = pd.DataFrame(result.result_rows, columns=list(result.column_names))
    print(f"  📊 DataFrame: {len(df):,} 行 × {len(df.columns)} 列")
    print()

    if len(df) == 0:
        print("⚠️ 无数据")
        return

    # 按场景ID去重
    before = len(df)
    df = df.drop_duplicates(subset=["场景ID"])
    print(f"  按场景ID去重: {before} → {len(df)}")
    print()

    print(f"💾 保存到 {OUTPUT_PATH}...")
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    df.to_parquet(OUTPUT_PATH, index=False)
    size_mb = os.path.getsize(OUTPUT_PATH) / 1024 / 1024
    print(f"  📏 大小: {size_mb:.1f} MB")
    print()

    print(f"✅ 完成！{len(df):,} 条彩蛋映射 → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
