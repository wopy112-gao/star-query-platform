"""星宝数据平台 — 结果交叉验证器（P3）

验证查询结果的合理性，通过自动生成锚点查询做交叉比对。
"""

from __future__ import annotations

import json
import re
from typing import Optional

# ============================================================
# SQL 解析工具
# ============================================================

def _remove_group_by(sql: str) -> str:
    """去掉 GROUP BY 子句，改为 COUNT(DISTINCT 场景ID) 聚合"""
    # 找到 GROUP BY
    gb_match = re.search(r"\bGROUP\s+BY\s+.+?(?:\bORDER\s+BY\b|\bLIMIT\b|\bHAVING\b|;|\Z)", sql, re.IGNORECASE | re.DOTALL)
    if not gb_match:
        return sql  # 没有 GROUP BY，无需处理

    gb_part = gb_match.group(0)
    after_gb = sql[sql.index(gb_part) + len(gb_part):]

    # 构建锚点 SQL：SELECT COUNT(DISTINCT 场景ID) AS 全量场景数 FROM data WHERE ...
    where_match = re.search(r"\bWHERE\b", sql, re.IGNORECASE)
    from_match = re.search(r"\bFROM\s+\w+(?:\s+\w+)?", sql, re.IGNORECASE)

    if not from_match:
        return sql

    from_clause = from_match.group(0)
    where_clause = ""
    if where_match:
        # WHERE 到 GROUP BY 之间的部分
        where_end = sql.index(gb_part)
        where_clause = sql[where_match.start():where_end]

    order_by_clause = ""
    order_match = re.search(r"\bORDER\s+BY\b.+(?:LIMIT\b|;|\Z)", after_gb, re.IGNORECASE)
    if order_match:
        order_by_clause = re.split(r"\bLIMIT\b", order_match.group(0), flags=re.IGNORECASE)[0]
    
    anchor_sql = f"SELECT COUNT(DISTINCT 场景ID) AS 全量场景数 {from_clause} {where_clause}".strip()
    # 去掉多余空格
    anchor_sql = re.sub(r"\s+", " ", anchor_sql).strip()
    return anchor_sql


def _remove_condition(sql: str) -> str:
    """去掉所有 WHERE 条件，只保留 FROM + 基础 SELECT"""
    from_match = re.search(r"\bFROM\s+\w+(?:\s+\w+)?", sql, re.IGNORECASE)
    if not from_match:
        return sql

    from_clause = from_match.group(0)

    # 判断是否有明细查询（SELECT *）还是聚合查询
    select_part = sql[:from_match.start()].strip()

    anchor_sql = f"SELECT COUNT(DISTINCT 场景ID) AS 全量场景数 {from_clause}"
    # 去掉多余空格
    anchor_sql = re.sub(r"\s+", " ", anchor_sql).strip()
    # 去掉 ORDER BY / LIMIT
    anchor_sql = re.split(r"\bORDER\s+BY\b", anchor_sql, flags=re.IGNORECASE)[0]
    anchor_sql = re.split(r"\bLIMIT\b", anchor_sql, flags=re.IGNORECASE)[0]
    return anchor_sql.strip()


def _extract_total_from_rows(rows: list[dict]) -> Optional[float]:
    """从行数据中提取第一个数值列的值（单行单列时视为总量）"""
    if not rows or len(rows) != 1:
        return None
    for key, val in rows[0].items():
        if isinstance(val, (int, float)) and key in ("全量场景数",):
            return float(val)
    # 回退：取第一个数值
    for key, val in rows[0].items():
        if isinstance(val, (int, float)):
            return float(val)
    return None


def _sum_dimension_values(rows: list[dict]) -> Optional[float]:
    """计算维度分布表中各行的数值列之和"""
    if not rows:
        return None
    # 找到第一个数值列
    total = 0.0
    for row in rows:
        for key, val in row.items():
            if isinstance(val, (int, float)) and key not in ("排名", "rank"):
                total += float(val)
                break
    return total


# ============================================================
# 交叉验证逻辑
# ============================================================

def validate_with_anchor(
    question: str,
    intent: Optional[dict],
    sql: str,
    rows: list[dict],
    engine,
) -> list[str]:
    """结果交叉验证主函数

    自动判断查询类型并生成锚点查询做交叉比对。

    Args:
        question: 原始用户问题
        intent: 意图拆解结果（dict 格式）
        sql: 执行的 SQL
        rows: 查询结果行
        engine: 查询引擎（有 .execute() 方法）

    Returns:
        异常警告列表（空 = 全部通过）
    """
    warnings: list[str] = []
    if not rows or not sql:
        return warnings

    if intent:
        intent = intent.get("intent") if isinstance(intent, dict) and "intent" in intent else intent

    # ---- 检查1：维度分布验证 ----
    # 适用场景：有 GROUP BY 的分布查询（各省、各城市等）
    has_group_by = bool(re.search(r"\bGROUP\s+BY\b", sql, re.IGNORECASE))
    is_distribution = has_group_by and len(rows) >= 2

    if is_distribution:
        # ---- 跳过 UNNEST 查询的交叉验证 ----
        # UNNEST 将多值字段拆成多行，维度之和必然 > 全量，校验无意义
        if "UNNEST" in sql.upper():
            pass  # 跳过
        else:
            anchor_sql = _remove_group_by(sql)
            if anchor_sql != sql:
                try:
                    anchor_result = engine.execute(anchor_sql)
                    if anchor_result["success"] and anchor_result["rows"]:
                        total = _extract_total_from_rows(anchor_result["rows"])
                        dim_sum = _sum_dimension_values(rows)

                        if total is not None and dim_sum is not None and total > 0:
                            diff_pct = abs(dim_sum - total) / total * 100
                            if diff_pct > 5:
                                warnings.append(
                                    f"交叉验证：各维度之和({dim_sum:.0f})与全量({total:.0f})偏差 {diff_pct:.1f}%，"
                                    f"可能统计口径不一致"
                                )
            except Exception as e:
                print(f"[交叉验证] 锚点查询失败: {e}")

    # ---- 检查2：筛选条件验证 ----
    # 适用场景：有 WHERE 条件的查询
    has_where = bool(re.search(r"\bWHERE\b", sql, re.IGNORECASE))
    is_filtered = has_where and not is_distribution

    if is_filtered and len(rows) >= 1:
        # 提取第一条结果的数值
        first_val = None
        for row in rows[:1]:
            for key, val in row.items():
                if isinstance(val, (int, float)):
                    first_val = float(val)
                    break

        if first_val is not None and first_val > 0:
            anchor_sql = _remove_condition(sql)
            if anchor_sql != sql:
                try:
                    anchor_result = engine.execute(anchor_sql)
                    if anchor_result["success"] and anchor_result["rows"]:
                        total = _extract_total_from_rows(anchor_result["rows"])
                        if total is not None and total > 0 and first_val > total:
                            warnings.append(
                                f"交叉验证：筛选后结果({first_val:.0f})大于全量({total:.0f})，"
                                f"可能筛选条件未正确应用"
                            )
                except Exception as e:
                    print(f"[交叉验证] 锚点查询(无筛选)失败: {e}")

    # ---- 检查3：TOP N 占比合理性 ----
    # 适用场景：带 LIMIT 的分布查询
    has_limit = bool(re.search(r"\bLIMIT\b", sql, re.IGNORECASE))
    if is_distribution and has_limit and len(rows) >= 3:
        # 如果 TOP N 的占比异常集中（TOP1 > 80%），可能有问题
        dim_sum = _sum_dimension_values(rows)
        if dim_sum and dim_sum > 0:
            # 找第一行
            for row in rows[:1]:
                for key, val in row.items():
                    if isinstance(val, (int, float)) and key not in ("排名", "rank"):
                        top_ratio = float(val) / dim_sum * 100
                        if top_ratio > 99:
                            warnings.append(
                                f"交叉验证：TOP1 占比 {top_ratio:.1f}%（过于集中），"
                                f"可能数据被过度筛选"
                            )
                        break

    return warnings
