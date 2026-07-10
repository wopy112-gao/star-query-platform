#!/usr/bin/env python3
"""
clickhouse-daily-egg-merge.py — 彩蛋数据同步 + 合并到全量 parquet

每天在主数据同步之后运行：
1. 拉取彩蛋映射表（clickhouse-egg-sync.py）
2. merge 到全量 parquet
3. 重启后端
"""

import os
import sys
import time
import subprocess
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EGG_SYNC_SCRIPT = os.path.join(SCRIPT_DIR, "clickhouse-egg-sync.py")
FULL_PARQUET = "/root/All_data_ch_full.parquet"
EGG_MAPPING = os.path.join(SCRIPT_DIR, "data", "egg_mapping.parquet")
EGG_COLS = ["彩蛋任务ID", "彩蛋药品名称", "彩蛋标题", "是否分子1=是(发分)", "命中原因"]


def main():
    t0 = time.time()
    print(f"┌────────────────────────────────────────────┐")
    print(f"│  星宝彩蛋数据每日同步+合并                   │")
    print(f"└────────────────────────────────────────────┘")
    print()

    # Step 1: 拉彩蛋映射
    print("📥 Step 1/3: 拉取彩蛋映射...")
    result = subprocess.run(
        [sys.executable, EGG_SYNC_SCRIPT],
        capture_output=True, text=True, cwd=SCRIPT_DIR,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(f"❌ 拉取失败: {result.stderr}")
        sys.exit(1)

    # Step 2: merge 到全量
    print("🔗 Step 2/3: merge 到全量 parquet...")
    if not os.path.exists(EGG_MAPPING):
        print(f"   ⚠️ 彩蛋映射文件不存在: {EGG_MAPPING}")
        sys.exit(1)

    full = pd.read_parquet(FULL_PARQUET)
    egg = pd.read_parquet(EGG_MAPPING)

    full["场景ID"] = full["场景ID"].astype("int64")
    egg["场景ID"] = egg["场景ID"].astype("int64")

    # 删除旧彩蛋列重新merge（覆盖最新数据）
    full = full.drop(columns=[c for c in EGG_COLS if c in full.columns], errors="ignore")
    full = full.merge(egg, on="场景ID", how="left")

    full["彩蛋任务ID"] = full["彩蛋任务ID"].fillna(0).astype("int64")
    full["彩蛋药品名称"] = full["彩蛋药品名称"].fillna("")
    full["彩蛋标题"] = full["彩蛋标题"].fillna("")
    full["是否分子1=是(发分)"] = full["是否分子1=是(发分)"].fillna(0).astype("int64")
    full["命中原因"] = full["命中原因"].fillna("")

    full.to_parquet(FULL_PARQUET, index=False)
    has_egg = (full["彩蛋任务ID"] > 0).sum()
    is_mol = (full["是否分子1=是(发分)"] == 1).sum()
    size_mb = os.path.getsize(FULL_PARQUET) / 1024 / 1024
    print(f"   ✅ 合并完成: {has_egg:,} 条彩蛋, {is_mol:,} 条分子")
    print(f"   📏 {size_mb:.1f} MB, {len(full):,} 行, {len(full.columns)} 列")
    print()

    # Step 3: 重建持久化 DuckDB
    print("📦 Step 3/4: 重建持久化 DuckDB...")
    db_result = subprocess.run(
        [sys.executable, "-c", """
import duckdb, os
DB_FILE = '/tmp/star-query.duckdb'
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)
conn = duckdb.connect(DB_FILE)
conn.execute("SET memory_limit='2GB'")
conn.execute("CREATE TABLE data AS SELECT * FROM read_parquet('/root/All_data_ch_full.parquet')")
rows = conn.execute('SELECT count(*) FROM data').fetchone()[0]
cols = len(conn.execute('SELECT * FROM data LIMIT 1').fetchdf().columns)
print(f'  重建完成: {rows:,} 行, {cols} 列')
conn.close()
"""],
        capture_output=True, text=True,
    )
    print(db_result.stdout.strip())
    if db_result.returncode != 0:
        print(f"  ⚠️ 重建失败: {db_result.stderr}")
    print()

    # Step 4: 重启
    print("🔄 Step 4/4: 重启后端...")
    restart_script = os.path.join(SCRIPT_DIR, "safe-restart.sh")
    result = subprocess.run(
        ["bash", restart_script, "--prod"],
        capture_output=True, text=True, cwd=SCRIPT_DIR,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(f"❌ 重启失败: {result.stderr}")
        sys.exit(1)

    print(f"✅ 全部完成！总耗时: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
