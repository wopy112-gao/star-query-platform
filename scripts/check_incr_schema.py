#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增量 parquet schema 校验（Step 7 防复发）
========================================
背景：2026-07-24 曾因增量列结构不匹配导致构建问题；2026-08-10 又因
      字段名含 '=' （是否分子1=是(发分)）引发 SQL 解析错误。
本脚本在 daily-sync 拉取增量后、合并前执行，防止列结构漂移静默进入全量。

用法:
  check_incr_schema.py <incr.parquet> <schema_snapshot.json>

行为:
  - 列数 != 54            → exit 2 (SCHEMA_ERROR)
  - 缺必需列              → exit 2 (SCHEMA_ERROR)
  - 与上次快照列集合不同  → exit 3 (SCHEMA_CHANGED)
  - 首次运行(无快照)      → 生成快照, exit 0
  - 全部通过              → exit 0 (SCHEMA_OK)

退出码供 shell 判断: 0=OK, 2=结构错误, 3=结构漂移, 4=其他
"""
import json
import sys
from datetime import datetime

import pyarrow.parquet as pq

EXPECTED_COLS = 54
REQUIRED_COLS = [
    "ydate",          # 合并依赖
    "场景ID",
    "原始场景ID",     # 彩蛋回填依赖（分母表 scenario_id 对应此列）
    "彩蛋任务ID",
    "是否分子1=是(发分)",
    "会话ID",
    "门店ID",
    "店员ID",
]


def main() -> int:
    if len(sys.argv) != 3:
        print("用法: check_incr_schema.py <incr.parquet> <schema_snapshot.json>")
        return 4

    incr_path, snapshot_path = sys.argv[1], sys.argv[2]

    try:
        f = pq.ParquetFile(incr_path)
        cols = list(f.schema_arrow.names)
    except Exception as e:  # noqa: BLE001
        print(f"SCHEMA_ERROR: 无法读取 parquet: {e}")
        return 4

    errors = []
    if len(cols) != EXPECTED_COLS:
        errors.append(f"列数 {len(cols)} != 预期 {EXPECTED_COLS}")

    missing = [c for c in REQUIRED_COLS if c not in cols]
    if missing:
        errors.append(f"缺必需列: {missing}")

    if errors:
        print("SCHEMA_ERROR: " + "; ".join(errors))
        return 2

    # 与快照对比（首次自动生成基线）
    try:
        import os
        if os.path.exists(snapshot_path):
            with open(snapshot_path, "r", encoding="utf-8") as fp:
                prev = json.load(fp)
            if prev.get("cols") != cols:
                added = sorted(set(cols) - set(prev.get("cols", [])))
                removed = sorted(set(prev.get("cols", [])) - set(cols))
                print(f"SCHEMA_CHANGED: added={added} removed={removed}")
                return 3
        else:
            with open(snapshot_path, "w", encoding="utf-8") as fp:
                json.dump({
                    "cols": cols,
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "source": incr_path,
                }, fp, ensure_ascii=False, indent=2)
            print(f"SCHEMA_BASELINE: 已生成基线快照 ({len(cols)} 列)")
    except Exception as e:  # noqa: BLE001
        print(f"SCHEMA_ERROR: 快照读写失败: {e}")
        return 4

    print(f"SCHEMA_OK: {len(cols)} 列, 与基线一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
