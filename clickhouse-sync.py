#!/usr/bin/env python3
"""
clickhouse-sync.py — 星宝数据平台 ClickHouse 数据同步工具

职责：从阿里云 ClickHouse（只读）拉取数据，转成 parquet，供星宝平台加载。
使用方式：
  python3 clickhouse-sync.py --mode full     # 首次全量
  python3 clickhouse-sync.py --mode daily    # 每日增量（ydate=yesterday）
  python3 clickhouse-sync.py --mode daily --date 2026-05-27  # 指定日期

与 update-data.sh 的关系：
  本脚本只负责"拉数据+转 parquet"，不负责重启后端。
  数据落地后，由 update-data.sh 或 safe-restart.sh 接手更新配置和重启。
"""

import argparse
import os
import sys
import time
from datetime import datetime, date, timedelta

import clickhouse_connect
import pandas as pd

# ============================================================
# 配置
# ============================================================

CH_HOST = "cc-2ze4vp6kio9ns5605.public.clickhouse.ads.aliyuncs.com"
CH_PORT = 8123
CH_USER = "yaoxin_ai_select"
CH_PASS = "4-s7D4HHcR8df3fh8kSO"
CH_DB = "yaoxin_ai"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "data")

# ============================================================
# SQL 模板（同事提供的完整查询，参数化 ydate 范围）
# ============================================================

SYNC_SQL = """
WITH
ai_conversation AS (
    SELECT
        conversation_id,
        ac_addtime,
        toUnixTimestamp(ac_addtime, 'Asia/Shanghai') + 8 * 3600 AS uac_addtime,
        count(1) AS au_nums,
        sum(file_duration) AS seconds
    FROM yaoxin_ai.x_ai_assistant_conversation_au
    GROUP BY conversation_id, ac_addtime
),
ai_scenario_chek AS (
    SELECT
        conversation.conversation_id                                     AS `会话ID`,
        scenario_check.sid                                               AS `场景ID`,
        member_id                                                        AS `店员ID`,
        store_id                                                         AS `门店ID`,
        IF(scenario_check.deal_status = 1, '是', '否')                  AS `交易是否达成`,
        scenario_check.transaction_failure_reason                        AS `交易失败原因`,
        scenario_check.ask_medicine_by_name                              AS `顾客点名药品`,
        scenario_check.conversation_brings_up_medicine                   AS `场景提及药品`,
        scenario_check.order_drugs                                       AS `订单药品`,
        IF(scenario_check.key_information_arrival = 1, '是', '否')      AS `是否关键信息到达`,
        IF(scenario_check.clerk_inquired_symptoms = 1, '是', '否')      AS `是否问症`,
        scenario_check.symptom_presentation                              AS `问症表现`,
        scenario_check.key_information_arrival_performance               AS `关键信息表现`,
        scenario_check.order_performance                                 AS `订单达成表现`,
        scenario_check.patient_tags                                      AS `患者关键信息`,
        scenario_check.scenario_end_time - scenario_check.scenario_start_time AS cha_sec,
        scenario_check.scenario_end_time,
        scenario_check.scenario_start_time,
        if(cha_sec < 0, scenario_check.scenario_end_time, scenario_check.scenario_start_time)   AS scenario_start_time_fixed,
        if(cha_sec < 0, scenario_check.scenario_start_time, scenario_check.scenario_end_time)   AS scenario_end_time_fixed,
        conversation.ac_addtime                                           AS `会话开始时间`,
        concat(
            leftPad(toString(floor(scenario_start_time_fixed / 3600)), 2, '0'), ':',
            leftPad(toString(floor((scenario_start_time_fixed % 3600) / 60)), 2, '0'), ':',
            leftPad(toString(scenario_start_time_fixed % 60), 2, '0')
        )                                                                AS `场景从会话的开始时间`,
        scenario_end_time_fixed - scenario_start_time_fixed               AS `场景时长`,
        toDateTime(toUInt32(conversation.uac_addtime + scenario_start_time_fixed)) AS `场景自然开始时间`,
        left(
            toString(toDateTime(toUInt32(conversation.uac_addtime + scenario_start_time_fixed) - 3600 * 8)),
            10
        )                                                                AS `场景日期`,
        toDate(toUInt32(conversation.uac_addtime + scenario_start_time_fixed)) AS ydate,
        IF(scenario_check.drug_combination = 1, '是', '否')              AS `是否联合用药`,
        IF(scenario_check.drug_combination_reasonable = 1, '是', '否')   AS `联合用药合理性`,
        CASE scenario_check.patient_gender
            WHEN 1 THEN '男'
            WHEN 2 THEN '女'
            ELSE '不可识别'
        END                                                              AS `顾客性别`,
        replace(scenario_check.patient_age, ' ', '')                     AS `顾客年龄`,
        CASE scenario_check.customer_trust_score
            WHEN 1 THEN '非常信任'
            WHEN 2 THEN '有一定信任，可沟通'
            ELSE '不信任'
        END                                                              AS `顾客信任度`,
        scenario_check.province                                           AS `省份`,
        scenario_check.city                                               AS `城市`,
        scenario_check.company_name                                       AS `连锁`,
        scenario_check.store_name                                         AS `门店`,
        CASE scenario_check.medicine_recommendation_under_scenario
            WHEN 2 THEN '未知'
            WHEN 1 THEN '是'
            ELSE '否'
        END                                                              AS `是否场景下活动推荐`,
        CASE scenario_check.activity_participation
            WHEN 2 THEN '未知'
            WHEN 1 THEN '是'
            ELSE '否'
        END                                                              AS `活动是否参与`,
        scenario_check.proportion_activity_introduction_percent           AS `活动时间占比`,
        CASE scenario_check.customer_activity_satisfaction
            WHEN 1 THEN '满意'
            WHEN 2 THEN '一般'
            WHEN 3 THEN '不满意'
            ELSE '非常不满意'
        END                                                              AS `活动满意度`,
        scenario_check.introduction_activity                              AS `活动介绍`,
        if(scenario_check.edit_user LIKE '%自动化%', 'AI自动化', '人工') AS `场景解析来源`,
        -- ↓↓↓ 2026-06-11 新增字段（同事 CH 库更新）↓↓↓
        scenario_check.clerk_mentioned_drugs                                    AS `店员提及药品JSON`,
        scenario_check.clerk_recommended_drugs                                   AS `店员推荐药品JSON`,
        scenario_check.user_age                                                  AS `用药人年龄分层`,
        scenario_check.combined_medication_action                                AS `联合用药动作`,
        scenario_check.recommended_combination_drugs                             AS `推荐的联合用药JSON`,
        scenario_check.overall                                                   AS `综合置信度评分`,
        scenario_check.scene_completeness                                        AS `场景完整度`,
        scenario_check.confidence                                                AS `业务置信度`,
        scenario_check.is_business                                               AS `是否商用`,
        scenario_check.confidence_score                                          AS `切割置信度分值`,
        scenario_check.completeness_score                                        AS `切割完整度分值`,
        -- ↑↑↑ 新增字段结束 ↑↑↑
        arrayDistinct(JSONExtract(scenario_check.exd_disease, 'Array(String)'))     AS exd_disease,
        arrayDistinct(JSONExtract(scenario_check.exd_disease_gate, 'Array(String)')) AS exd_disease_gate
    FROM yaoxin_ai.x_ai_assistant_scenario_check scenario_check
    LEFT JOIN ai_conversation conversation
        ON conversation.conversation_id = scenario_check.conversation_id
    WHERE 1
),
select_all_disease AS (
    SELECT
        exd_disease_name                         AS `疾病名称`,
        `会话ID`,
        `场景ID`,
        `店员ID`,
        `门店ID`,
        `交易是否达成`,
        `交易失败原因`,
        `顾客点名药品`,
        `场景提及药品`,
        `订单药品`,
        `是否问症`,
        `是否关键信息到达`,
        `问症表现`,
        `关键信息表现`,
        `订单达成表现`,
        `患者关键信息`,
        `会话开始时间`,
        `场景从会话的开始时间`,
        `场景时长`,
        `场景自然开始时间`,
        `场景日期`,
        ydate,
        `是否联合用药`,
        `联合用药合理性`,
        `顾客性别`,
        `顾客年龄`,
        `顾客信任度`,
        `省份`,
        `城市`,
        `连锁`,
        `门店`,
        `是否场景下活动推荐`,
        `活动是否参与`,
        `活动时间占比`,
        `活动满意度`,
        `活动介绍`,
        `场景解析来源`,
        -- ↓↓↓ 2026-06-11 新增字段 ↓↓↓
        `店员提及药品JSON`,
        `店员推荐药品JSON`,
        `用药人年龄分层`,
        `联合用药动作`,
        `推荐的联合用药JSON`,
        `综合置信度评分`,
        `场景完整度`,
        `业务置信度`,
        `是否商用`,
        `切割置信度分值`,
        `切割完整度分值`
        -- ↑↑↑ 新增字段结束 ↑↑↑
    FROM ai_scenario_chek ck
    ARRAY JOIN exd_disease AS exd_disease_name
    WHERE 1
)
SELECT *
FROM select_all_disease
{where_clause}
ORDER BY `场景ID` ASC
"""


# ============================================================
# 核心逻辑
# ============================================================

def get_client():
    """创建 ClickHouse 只读连接"""
    return clickhouse_connect.get_client(
        host=CH_HOST,
        port=CH_PORT,
        username=CH_USER,
        password=CH_PASS,
    )


def fetch_data(client, date_start=None, date_end=None):
    """按日期范围从 CH 拉取数据。date_start/date_end 均为 None 则拉全量（不过滤）"""
    if date_start and date_end:
        where_clause = f"WHERE ydate BETWEEN '{date_start}' AND '{date_end}'"
        print(f"  SQL: ydate BETWEEN {date_start} AND {date_end}")
    else:
        where_clause = ""
        print(f"  SQL: 全量（无日期过滤）")
    sql = SYNC_SQL.format(where_clause=where_clause)
    t0 = time.time()
    result = client.query(sql)
    elapsed = time.time() - t0
    print(f"  ⏱  CH查询耗时: {elapsed:.1f}秒")
    print(f"  📦 拉取行数: {result.row_count:,}")

    # 转成 pandas DataFrame
    df = pd.DataFrame(
        result.result_rows,
        columns=list(result.column_names),
    )
    print(f"  📊 DataFrame: {len(df):,} 行 × {len(df.columns)} 列")
    return df


def clean_data(df):
    """清洗异常数据
    注：ydate=1970-01-01 的数据不在此过滤，保留原始数据给应用层处理。
    当前仅做格式/类型方面的轻量修正。
    """
    print(f"  ✅ 数据保持原样（{len(df):,} 行），未做过滤")
    return df


def save_parquet(df, output_path):
    """保存为 parquet"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    t0 = time.time()
    df.to_parquet(output_path, index=False)
    elapsed = time.time() - t0
    size_mb = os.path.getsize(output_path) / 1024 / 1024
    print(f"  💾 输出: {output_path}")
    print(f"  📏 大小: {size_mb:.1f} MB")
    print(f"  ⏱  写入耗时: {elapsed:.1f}秒")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="星宝 ClickHouse 数据同步")
    parser.add_argument(
        "--mode",
        choices=["full", "daily"],
        default="daily",
        help="full=全量拉取, daily=增量拉取（默认昨日）",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="指定拉取日期（YYYY-MM-DD，仅 daily 模式有效），默认昨天",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="输出 parquet 路径。默认 full→data/全量_{日期}.parquet, daily→data/增量_{日期}.parquet",
    )
    args = parser.parse_args()

    # ---- 确定日期范围 ----
    if args.mode == "full":
        # 全量：不过滤日期，含 ydate=1970-01-01 的数据
        date_start = None
        date_end = None
        today_str = date.today().isoformat()
        default_output = os.path.join(OUTPUT_DIR, f"全量_{today_str}.parquet")
    else:
        if args.date:
            target_date = args.date
        else:
            target_date = (date.today() - timedelta(days=1)).isoformat()
        date_start = target_date
        date_end = target_date
        default_output = os.path.join(OUTPUT_DIR, f"增量_{target_date}.parquet")

    output_path = args.output or default_output

    print(f"┌────────────────────────────────────────────┐")
    print(f"│  星宝 ClickHouse 数据同步                  │")
    print(f"│  模式: {args.mode:>8s}                      │")
    date_label = f"{date_start} ~ {date_end}" if date_start else "全量（无日期过滤）"
    print(f"│  日期: {date_label:<28s}│")
    print(f"└────────────────────────────────────────────┘")
    print()

    # ---- 第一步：连接 CH ----
    print("📡 连接 ClickHouse...")
    client = get_client()
    print(f"  ✅ 连接成功 (v{client.server_version})")
    print()

    # ---- 第二步：拉取数据 ----
    print("📥 拉取数据...")
    df = fetch_data(client, date_start=date_start, date_end=date_end)
    print()

    if len(df) == 0:
        print("⚠️ 无数据，跳过保存")
        return

    # ---- 第三步：清洗 ----
    print("🧹 清洗数据...")
    df = clean_data(df)
    print()

    # ---- 第四步：保存 parquet ----
    print("💾 保存...")
    save_parquet(df, output_path)
    print()

    print(f"✅ 完成！{len(df):,} 行 → {output_path}")
    print(f"   下一步: cp 到星宝 data 目录 + 重启后端")


if __name__ == "__main__":
    main()
