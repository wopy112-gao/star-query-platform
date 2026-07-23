#!/usr/bin/env python3
"""
星宝数据平台 — 全量数据修复脚本
按月分片从 ClickHouse 全量重拉（含 ARRAY JOIN 展开）+ 彩蛋合并 + DuckDB 重建
执行说明：
  1. 先备份全量文件和彩蛋映射
  2. 按月拉取 + 合并
  3. 彩蛋 merge
  4. 重建 DuckDB + 安全重启（调用 safe-restart.sh）
"""

import os
import sys
import time
import subprocess
import clickhouse_connect
import pandas as pd

# ============================================================
# 配置
# ============================================================
CH_HOST = os.environ.get("CH_HOST", "cc-2ze4vp6kio9ns5605.public.clickhouse.ads.aliyuncs.com")
CH_PORT = int(os.environ.get("CH_PORT", "8123"))
CH_USER = os.environ.get("CH_USER", "yaoxin_ai_select")
CH_PASS = os.environ.get("CH_PASS", "4-s7D4HHcR8df3fh8kSO")

STAR_DIR = "/root/.lightclaw/workspace/star-query"
FULL_FILE = "/root/All_data_ch_full.parquet"
MERGED_FILE = "/root/All_data_ch_full_merged.parquet"
EGG_FILE = os.path.join(STAR_DIR, "data", "egg_mapping.parquet")
LOG_FILE = f"/var/log/star-query-restore-{time.strftime('%Y%m%d_%H%M%S')}.log"

# 按月分片（按 ClickHouse 统计的实际数据月份）
MONTHS = [
    "2025-08", "2025-10", "2025-11", "2025-12",
    "2026-01", "2026-02", "2026-03", "2026-04",
    "2026-05", "2026-06", "2026-07",
]

# 同步 SQL 模板（与 clickhouse-sync.py 一致）
SYNC_SQL_TPL = """
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
        scenario_check.original_scenario_id                                        AS `原始场景ID`,
        arrayDistinct(JSONExtract(scenario_check.exd_disease, 'Array(String)'))     AS exd_disease
    FROM yaoxin_ai.x_ai_assistant_scenario_check scenario_check
    LEFT JOIN ai_conversation conversation
        ON conversation.conversation_id = scenario_check.conversation_id
),
select_all_disease AS (
    SELECT
        exd_disease_name                         AS `疾病名称`,
        `会话ID`, `场景ID`, `店员ID`, `门店ID`,
        `交易是否达成`, `交易失败原因`, `顾客点名药品`, `场景提及药品`, `订单药品`,
        `是否问症`, `是否关键信息到达`,
        `问症表现`, `关键信息表现`, `订单达成表现`, `患者关键信息`,
        `会话开始时间`, `场景从会话的开始时间`, `场景时长`, `场景自然开始时间`,
        `场景日期`, ydate,
        `是否联合用药`, `联合用药合理性`, `顾客性别`, `顾客年龄`, `顾客信任度`,
        `省份`, `城市`, `连锁`, `门店`,
        `是否场景下活动推荐`, `活动是否参与`, `活动时间占比`, `活动满意度`, `活动介绍`,
        `场景解析来源`,
        `店员提及药品JSON`, `店员推荐药品JSON`, `用药人年龄分层`, `联合用药动作`,
        `推荐的联合用药JSON`, `综合置信度评分`, `场景完整度`, `业务置信度`, `是否商用`,
        `切割置信度分值`, `切割完整度分值`, `原始场景ID`
    FROM ai_scenario_chek ck
    ARRAY JOIN exd_disease AS exd_disease_name
)
SELECT *
FROM select_all_disease
{where_clause}
ORDER BY `场景ID` ASC
"""


def log(msg):
    """日志输出"""
    print(msg)
    with open(LOG_FILE, "a") as f:
        f.write(msg + "\n")


def get_client():
    """创建 CH 连接（带超时设置）"""
    return clickhouse_connect.get_client(
        host=CH_HOST, port=CH_PORT,
        username=CH_USER, password=CH_PASS,
        connect_timeout=30,
        send_receive_timeout=120,  # 每次查询最多 2 分钟
    )


def fetch_month(client, ym):
    """按月拉取数据"""
    year, month = ym.split("-")
    start_date = f"{ym}-01"
    if month == "12":
        end_date = f"{int(year)+1}-01-01"
    else:
        end_date = f"{year}-{int(month)+1:02d}-01"

    where_clause = f"WHERE ydate >= '{start_date}' AND ydate < '{end_date}'"
    sql = SYNC_SQL_TPL.format(where_clause=where_clause)

    t0 = time.time()
    log(f"    查询执行中...")
    result = client.query(sql)
    elapsed = time.time() - t0

    df = pd.DataFrame(result.result_rows, columns=list(result.column_names))
    log(f"    耗时 {elapsed:.1f}s | {len(df):,} 行 | {len(df.columns)} 列")
    return df


def merge_month(full_path, month_df, ym):
    """将一个月的数据合并到全量（ydate 移除 + 合并，不去重）"""
    full = pd.read_parquet(full_path)

    incr_ydates = month_df["ydate"].unique()
    before = len(full)
    full_clean = full[~full["ydate"].isin(incr_ydates)]
    removed = before - len(full_clean)

    merged = pd.concat([full_clean, month_df], ignore_index=True)
    merged.to_parquet(MERGED_FILE, index=False)
    os.replace(MERGED_FILE, full_path)

    # 验证
    scene_counts = merged.groupby("场景ID").size()
    multi = (scene_counts > 1).sum()
    multi_rows = scene_counts[scene_counts > 1].sum()
    log(f"    → {len(merged):,} 行 | 移除 {removed} | "
        f"多疾病场景 {multi} (涉及 {multi_rows} 行)")
    return merged


def verify(full, stage=""):
    """验证数据完整性"""
    total = len(full)
    sids = full["场景ID"].nunique()
    scene_counts = full.groupby("场景ID").size()
    multi = (scene_counts > 1).sum()
    multi_rows = scene_counts[scene_counts > 1].sum()
    has_egg = (full["彩蛋任务ID"] > 0).sum() if "彩蛋任务ID" in full.columns else 0
    date_min = full["ydate"].min()
    date_max = full["ydate"].max()
    file_size = os.path.getsize(FULL_FILE) / 1024 / 1024

    summary = (
        f"  {stage}：{total:,} 行 | {sids:,} 场景 | "
        f"多疾病 {multi} 场景 ({multi_rows} 行) | "
        f"彩蛋 {has_egg:,} | {file_size:.0f} MB"
    )
    log(summary)
    return summary


def main():
    t_start = time.time()

    log("=" * 60)
    log(f"  星宝数据平台 — 全量数据修复")
    log(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 60)

    # ========================================
    # Step 1: 备份
    # ========================================
    log("")
    log("[Step 1/5] 备份当前全量和彩蛋映射...")
    backup_dir = f"/root/star-query-backup-{time.strftime('%Y%m%d_%H%M%S')}"
    os.makedirs(backup_dir, exist_ok=True)

    if os.path.exists(FULL_FILE):
        bak_path = os.path.join(backup_dir, "All_data_ch_full.parquet.bak")
        import shutil
        shutil.copy2(FULL_FILE, bak_path)
        log(f"  ✅ 全量备份: {bak_path}")
    if os.path.exists(EGG_FILE):
        bak_path = os.path.join(backup_dir, "egg_mapping.parquet.bak")
        shutil.copy2(EGG_FILE, bak_path)
        log(f"  ✅ 彩蛋备份: {bak_path}")

    # ========================================
    # Step 2: 按月分片拉取 + 合并
    # ========================================
    log("")
    log("[Step 2/5] 按月分片拉取数据...")
    client = get_client()
    log(f"  ✅ 连接 CH: v{client.server_version}")

    # 从空全量开始
    empty_df = pd.DataFrame()
    empty_df.to_parquet(FULL_FILE, index=False)

    for i, ym in enumerate(MONTHS, 1):
        log(f"")
        log(f"  [{i}/{len(MONTHS)}] {ym}...")
        try:
            month_df = fetch_month(client, ym)
            if len(month_df) == 0:
                log(f"     ⚠️ 无数据，跳过")
                continue
            merge_month(FULL_FILE, month_df, ym)
        except Exception as e:
            log(f"  ❌ {ym} 拉取失败: {e}")
            log(f"     已完成的月份不受影响，跳过 {ym} 继续下一个月")
            continue

    # 最终验证
    log("")
    full = pd.read_parquet(FULL_FILE)
    verify(full, "全量合并完成")

    # ========================================
    # Step 3: 拉取彩蛋映射 + merge
    # ========================================
    log("")
    log("[Step 3/5] 拉取彩蛋映射并 merge...")

    # 先拉取彩蛋映射
    egg_script = os.path.join(STAR_DIR, "clickhouse-egg-sync.py")
    result = subprocess.run(
        [sys.executable, egg_script],
        capture_output=True, text=True, cwd=STAR_DIR,
    )
    for line in result.stdout.split("\n"):
        if line.strip():
            log(f"  {line}")
    if result.returncode != 0:
        log(f"  ❌ 彩蛋拉取失败: {result.stderr}")
        # 彩蛋失败不中断，可以继续
    else:
        # merge 彩蛋到全量
        if os.path.exists(EGG_FILE):
            full = pd.read_parquet(FULL_FILE)
            egg = pd.read_parquet(EGG_FILE)

            full["场景ID"] = full["场景ID"].astype("int64")
            egg["场景ID"] = egg["场景ID"].astype("int64")

            EGG_COLS = ["彩蛋任务ID", "彩蛋药品名称", "彩蛋标题",
                        "是否分子1=是(发分)", "命中原因"]
            full = full.drop(columns=[c for c in EGG_COLS if c in full.columns],
                             errors="ignore")
            full = full.merge(egg, on="场景ID", how="left")

            full["彩蛋任务ID"] = full["彩蛋任务ID"].fillna(0).astype("int64")
            full["彩蛋药品名称"] = full["彩蛋药品名称"].fillna("")
            full["彩蛋标题"] = full["彩蛋标题"].fillna("")
            full["是否分子1=是(发分)"] = full["是否分子1=是(发分)"].fillna(0).astype("int64")
            full["命中原因"] = full["命中原因"].fillna("")

            full.to_parquet(FULL_FILE, index=False)
            has_egg = (full["彩蛋任务ID"] > 0).sum()
            is_mol = (full["是否分子1=是(发分)"] == 1).sum()
            log(f"  ✅ 彩蛋合并: {has_egg:,} 条彩蛋, {is_mol:,} 条分子")

    # ========================================
    # Step 4: 重建 DuckDB + 更新配置 + 重启
    # ========================================
    log("")
    log("[Step 4/5] 重建 DuckDB + 安全重启...")

    # 重建 DuckDB
    import duckdb
    DB_FILE = "/tmp/star-query.duckdb"
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
    conn = duckdb.connect(DB_FILE)
    conn.execute("SET memory_limit='2GB'")
    conn.execute(f"CREATE TABLE data AS SELECT * FROM read_parquet('{FULL_FILE}')")
    rows = conn.execute("SELECT count(*) FROM data").fetchone()[0]
    log(f"  ✅ DuckDB 重建: {rows:,} 行")
    conn.close()

    # 更新 schema_knowledge 行数
    full = pd.read_parquet(FULL_FILE)
    total_rows = len(full)
    schema_file = os.path.join(STAR_DIR, "backend", "schema_knowledge.py")
    import re
    with open(schema_file, "r") as f:
        content = f.read()
    content = re.sub(r'"total_rows":\s*\d+', f'"total_rows": {total_rows}', content)
    with open(schema_file, "w") as f:
        f.write(content)
    log(f"  ✅ schema_knowledge 更新: total_rows={total_rows}")

    # 安全重启
    log(f"  🔄 安全重启正式环境...")
    result = subprocess.run(
        ["bash", os.path.join(STAR_DIR, "safe-restart.sh"), "--prod"],
        capture_output=True, text=True, cwd=STAR_DIR,
    )
    for line in result.stdout.split("\n"):
        if line.strip():
            log(f"  {line}")
    if result.returncode != 0:
        log(f"  ❌ 重启失败: {result.stderr}")
        return 1

    # ========================================
    # Step 5: 验证
    # ========================================
    log("")
    log("[Step 5/5] 数据验证...")
    full = pd.read_parquet(FULL_FILE)

    total = len(full)
    sids = full["场景ID"].nunique()
    scene_counts = full.groupby("场景ID").size()
    multi = (scene_counts > 1).sum()
    multi_rows = scene_counts[scene_counts > 1].sum()
    disease_unique = full["疾病名称"].nunique()
    has_egg = (full["彩蛋任务ID"] > 0).sum()
    is_mol = (full["是否分子1=是(发分)"] == 1).sum()
    date_min = full["ydate"].min()
    date_max = full["ydate"].max()
    file_size = os.path.getsize(FULL_FILE) / 1024 / 1024

    log(f"  ✅ 总行数:       {total:,}")
    log(f"  ✅ 唯一场景ID:   {sids:,}")
    log(f"  ✅ 多疾病场景:   {multi:,} (涉及 {multi_rows:,} 行)")
    log(f"  ✅ 疾病名称数:   {disease_unique}")
    log(f"  ✅ 彩蛋覆盖:     {has_egg:,} 条 (分子 {is_mol:,} 条)")
    log(f"  ✅ 日期范围:     {date_min} ~ {date_max}")
    log(f"  ✅ 文件大小:     {file_size:.1f} MB")

    elapsed = time.time() - t_start
    log("")
    log("=" * 60)
    log(f" 🎉 全量数据修复完成！总耗时 {elapsed:.1f} 秒")
    log(f"    备份: {backup_dir}")
    log(f"    日志: {LOG_FILE}")
    log("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
