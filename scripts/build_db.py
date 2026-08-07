#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_db.py — 星宝 DuckDB 离线构建器（Step 3）

离线构建完整新库（7 张表含校验），独立进程，不碰线上文件。
跑在 DuckDB 1.5.5 上，与 sql_engine.py 的建表 SQL 保持一致。

用法:
    python3 build_db.py \
        --data /root/All_data_ch_full.parquet \
        --mapping /tmp/star-mapping/results/星宝药品ATC映射表_v1.xlsx \
        --output /root/star-query-build.duckdb \
        [--memory 4GB]

构建内容（与 sql_engine.py 完全一致）:
    data, pre_agg, disease_agg, monthly_agg,
    drug_index, drug_name_index, drug_atc_index

校验:
    - 7 张表全部存在
    - data 行数 == parquet 行数, 列数 == 54
    - pre_agg 应为 1 行
    - drug_atc_index ATC 匹配率统计
    - 输出 JSON 构建报告（供 Step 4 daily-sync 读取）
"""

import argparse
import json
import os
import sys
import time
import traceback

import duckdb
import pandas as pd

EXPECTED_TABLES = {
    "data", "pre_agg", "disease_agg", "monthly_agg",
    "drug_index", "drug_name_index", "drug_atc_index",
}
EXPECTED_COLS = 54


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def build(data_path: str, mapping_path: str, output_path: str, memory_limit: str) -> dict:
    """构建完整新库，返回构建报告 dict。"""
    report = {
        "status": "ok",
        "data_path": data_path,
        "mapping_path": mapping_path,
        "output_path": output_path,
        "duckdb_version": duckdb.__version__,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tables": {},
        "warnings": [],
    }

    # ---- 前置校验 ----
    if not os.path.exists(data_path):
        raise FileNotFoundError(f"数据源不存在: {data_path}")
    if not os.path.exists(mapping_path):
        raise FileNotFoundError(f"映射表不存在: {mapping_path}")

    # 输出目录
    out_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(out_dir, exist_ok=True)

    # 若输出文件已存在（上次构建残留），先备份后删除
    if os.path.exists(output_path):
        backup = f"{output_path}.bak-{int(time.time())}"
        os.rename(output_path, backup)
        report["warnings"].append(f"输出文件已存在，已备份为 {backup}")

    t_all = time.time()
    conn = duckdb.connect(output_path)
    conn.execute(f"SET memory_limit='{memory_limit}'")
    conn.execute("SET threads=4")
    log(f"DuckDB {duckdb.__version__} 打开: {output_path} (内存上限 {memory_limit})")

    # ---- Step 1: data 表 ----
    log("Step 1/4 构建 data 表...")
    t0 = time.time()
    conn.execute(f"CREATE TABLE data AS SELECT * FROM read_parquet('{data_path}')")
    data_rows = conn.execute("SELECT COUNT(*) FROM data").fetchone()[0]
    data_cols = len(conn.execute("DESCRIBE data").fetchall())
    report["tables"]["data"] = {"rows": data_rows, "cols": data_cols}
    log(f"  data: {data_rows:,} 行 × {data_cols} 列 ({time.time()-t0:.1f}s)")

    if data_cols != EXPECTED_COLS:
        report["warnings"].append(f"data 列数 {data_cols} != 预期 {EXPECTED_COLS}")

    # ---- Step 2: 预聚合 3 表 ----
    log("Step 2/4 构建预聚合表 (pre_agg / disease_agg / monthly_agg)...")
    t0 = time.time()

    conn.execute("""
        CREATE TABLE pre_agg AS
        SELECT
            COUNT(DISTINCT 场景ID) AS 总场景数,
            SUM(CASE WHEN 交易是否达成 = '是' THEN 1 ELSE 0 END) AS 成交数,
            COALESCE(SUM(CASE WHEN 交易是否达成 = '是' THEN 1 ELSE 0 END) * 1.0 /
                NULLIF(COUNT(DISTINCT 场景ID), 0), 0) AS 成交率,
            SUM(CASE WHEN 是否问症 = '是' THEN 1 ELSE 0 END) AS 问症数,
            COALESCE(SUM(CASE WHEN 是否问症 = '是' THEN 1 ELSE 0 END) * 1.0 /
                NULLIF(COUNT(DISTINCT 场景ID), 0), 0) AS 问症率,
            SUM(CASE WHEN 是否关键信息到达 = '是' THEN 1 ELSE 0 END) AS 关键信息到达数,
            COALESCE(SUM(CASE WHEN 是否关键信息到达 = '是' THEN 1 ELSE 0 END) * 1.0 /
                NULLIF(COUNT(DISTINCT 场景ID), 0), 0) AS 关键信息到达率,
            COUNT(DISTINCT 门店ID) AS 门店数,
            COUNT(DISTINCT 店员ID) AS 店员数
        FROM data
    """)
    pre_rows = conn.execute("SELECT COUNT(*) FROM pre_agg").fetchone()[0]
    report["tables"]["pre_agg"] = {"rows": pre_rows}

    conn.execute("""
        CREATE TABLE disease_agg AS
        SELECT
            疾病名称,
            COUNT(DISTINCT 场景ID) AS 场景数,
            SUM(CASE WHEN 交易是否达成 = '是' THEN 1 ELSE 0 END) AS 成交数,
            COALESCE(SUM(CASE WHEN 交易是否达成 = '是' THEN 1 ELSE 0 END) * 1.0 /
                NULLIF(COUNT(DISTINCT 场景ID), 0), 0) AS 成交率,
            SUM(CASE WHEN 是否问症 = '是' THEN 1 ELSE 0 END) AS 问症数,
            COALESCE(SUM(CASE WHEN 是否问症 = '是' THEN 1 ELSE 0 END) * 1.0 /
                NULLIF(COUNT(DISTINCT 场景ID), 0), 0) AS 问症率
        FROM data
        WHERE 疾病名称 IS NOT NULL AND 疾病名称 != ''
        GROUP BY 疾病名称
        ORDER BY 场景数 DESC
    """)
    disease_rows = conn.execute("SELECT COUNT(*) FROM disease_agg").fetchone()[0]
    report["tables"]["disease_agg"] = {"rows": disease_rows}

    conn.execute("""
        CREATE TABLE monthly_agg AS
        SELECT
            strftime(ydate, '%Y-%m') AS 月份,
            COUNT(DISTINCT 场景ID) AS 场景数,
            SUM(CASE WHEN 交易是否达成 = '是' THEN 1 ELSE 0 END) AS 成交数,
            COALESCE(SUM(CASE WHEN 交易是否达成 = '是' THEN 1 ELSE 0 END) * 1.0 /
                NULLIF(COUNT(DISTINCT 场景ID), 0), 0) AS 成交率
        FROM data
        WHERE ydate IS NOT NULL
        GROUP BY 月份
        ORDER BY 月份
    """)
    monthly_rows = conn.execute("SELECT COUNT(*) FROM monthly_agg").fetchone()[0]
    report["tables"]["monthly_agg"] = {"rows": monthly_rows}
    log(f"  pre_agg={pre_rows}行, disease_agg={disease_rows}行, monthly_agg={monthly_rows}行 ({time.time()-t0:.1f}s)")

    # ---- Step 3: 药品索引 3 表 ----
    log("Step 3/4 构建药品索引表 (drug_index / drug_name_index / drug_atc_index)...")
    t0 = time.time()

    conn.execute("""
        CREATE TABLE drug_index AS
        SELECT DISTINCT
            场景ID,
            TRIM(t.drug, ' "') AS 药品名,
            '场景提及药品' AS 来源字段
        FROM data,
        LATERAL UNNEST(string_split(TRIM(场景提及药品, '[]'), ',')) AS t(drug)
        WHERE 场景提及药品 IS NOT NULL AND 场景提及药品 != '[]' AND 场景提及药品 != ''

        UNION ALL

        SELECT DISTINCT
            场景ID,
            TRIM(t.drug, ' "') AS 药品名,
            '顾客点名药品' AS 来源字段
        FROM data,
        LATERAL UNNEST(string_split(TRIM(顾客点名药品, '[]'), ',')) AS t(drug)
        WHERE 顾客点名药品 IS NOT NULL AND 顾客点名药品 != '[]' AND 顾客点名药品 != ''

        UNION ALL

        SELECT DISTINCT
            场景ID,
            TRIM(t.drug, ' "') AS 药品名,
            '订单药品' AS 来源字段
        FROM data,
        LATERAL UNNEST(string_split(TRIM(订单药品, '[]'), ',')) AS t(drug)
        WHERE 订单药品 IS NOT NULL AND 订单药品 != '[]' AND 订单药品 != ''
        AND t.drug NOT LIKE '%未识别%'
    """)
    index_rows = conn.execute("SELECT COUNT(*) FROM drug_index").fetchone()[0]
    report["tables"]["drug_index"] = {"rows": index_rows}

    conn.execute("""
        CREATE TABLE drug_name_index AS
        SELECT 药品名, LIST(场景ID) AS 场景ID列表
        FROM drug_index
        GROUP BY 药品名
    """)
    name_rows = conn.execute("SELECT COUNT(*) FROM drug_name_index").fetchone()[0]
    report["tables"]["drug_name_index"] = {"rows": name_rows}

    # drug_mapping 用 DataFrame register（与 sql_engine.py 一致，不持久化为表）
    mapping_df = pd.read_excel(mapping_path)
    conn.register("drug_mapping", mapping_df)
    report["tables"]["drug_mapping"] = {"rows": len(mapping_df), "persisted": False}
    log(f"  映射表已加载: {len(mapping_df):,} 行")

    conn.execute("""
        CREATE TABLE drug_atc_index AS
        SELECT DISTINCT
            di.场景ID,
            di.药品名,
            di.来源字段,
            dm."ATC编码",
            dm."ATC第4级(化学亚组)" AS ATC化学亚组,
            dm."ATC第3级(药理亚组)" AS ATC药理亚组,
            dm."ATC第2级(治疗亚组)" AS ATC治疗亚组,
            dm."ATC第1级(解剖大类)" AS ATC解剖大类,
            dm."中西药分类",
            dm."置信度" AS 映射置信度
        FROM drug_index di
        LEFT JOIN drug_mapping dm ON di.药品名 = dm."原始药品名称"
    """)
    atc_rows = conn.execute("SELECT COUNT(*) FROM drug_atc_index").fetchone()[0]
    matched_rows = conn.execute(
        "SELECT COUNT(*) FROM drug_atc_index WHERE ATC编码 IS NOT NULL"
    ).fetchone()[0]
    match_rate = round(matched_rows / atc_rows * 100, 1) if atc_rows > 0 else 0
    report["tables"]["drug_atc_index"] = {"rows": atc_rows, "atc_matched": matched_rows, "atc_match_rate": match_rate}
    log(f"  drug_index={index_rows:,}行, drug_name_index={name_rows:,}行, "
        f"drug_atc_index={atc_rows:,}行 (ATC匹配率 {match_rate}%) ({time.time()-t0:.1f}s)")

    # ---- Step 4: 校验 ----
    log("Step 4/4 校验...")
    actual_tables = set(
        r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    )
    report["actual_tables"] = sorted(actual_tables)
    missing = EXPECTED_TABLES - actual_tables
    report["missing_tables"] = sorted(missing)

    checks = {
        "all_tables_present": len(missing) == 0,
        "data_rows_match_parquet": data_rows == report.get("parquet_rows"),
        "data_cols_54": data_cols == EXPECTED_COLS,
        "pre_agg_single_row": pre_rows == 1,
    }

    # parquet 行数基准（与 data 表对比）
    parquet_rows = conn.execute(
        f"SELECT COUNT(*) FROM read_parquet('{data_path}')"
    ).fetchone()[0]
    report["parquet_rows"] = parquet_rows
    report["row_diff"] = data_rows - parquet_rows

    checks["data_rows_match_parquet"] = data_rows == parquet_rows
    report["checks"] = checks

    if not all(checks.values()):
        report["status"] = "warning"
        report["warnings"].append(
            f"校验未全通过: {[k for k, v in checks.items() if not v]}"
        )

    # VACUUM 压缩
    log("VACUUM ANALYZE 压缩...")
    conn.execute("VACUUM ANALYZE")

    report["elapsed_sec"] = round(time.time() - t_all, 1)
    report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    report["status"] = "ok" if all(checks.values()) else "warning"

    conn.close()
    log(f"构建完成: {report['elapsed_sec']}s, 状态={report['status']}")
    return report


def main():
    parser = argparse.ArgumentParser(description="星宝 DuckDB 离线构建器")
    parser.add_argument("--data", required=True, help="全量 parquet 路径")
    parser.add_argument("--mapping", required=True, help="药品 ATC 映射表 xlsx 路径")
    parser.add_argument("--output", required=True, help="输出 .duckdb 文件路径")
    parser.add_argument("--memory", default="4GB", help="内存上限 (默认 4GB)")
    parser.add_argument("--report", default="", help="构建报告 JSON 输出路径（可选）")
    args = parser.parse_args()

    try:
        report = build(args.data, args.mapping, args.output, args.memory)
        if args.report:
            with open(args.report, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            log(f"报告已写入: {args.report}")
        # 打印摘要
        print("\n===== 构建报告摘要 =====")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        sys.exit(0 if report["status"] == "ok" else 2)
    except Exception as e:
        print(f"[ERROR] 构建失败: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
