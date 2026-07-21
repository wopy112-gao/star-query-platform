"""P2-1 UNNEST 改写单元测试

测试 _rewrite_drug_unnest_query() 方法：
- 无条件 UNNEST（场景提及/顾客点名/订单药品）
- 带条件 UNNEST（疾病/时间/地理）
- 成交场景数 UNNEST
- 非 UNNEST 查询（跳过）
- 结果合理性检查
"""

import sys
import time
from sql_engine import DuckDbEngine

PASS = 0
FAIL = 0

def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} — {detail}")


def test_rewrite_detection(e: DuckDbEngine):
    """测试改写检测逻辑"""
    print("\n=== 测试1: 改写检测 ===")

    cases = [
        # (sql, should_rewrite, description)
        (
            "SELECT t.drug AS 场景提及药品, COUNT(DISTINCT data.场景ID) AS 场景数 "
            "FROM data, LATERAL UNNEST(string_split(TRIM(场景提及药品, '[]'), ',')) AS t(drug) "
            "WHERE 场景提及药品 IS NOT NULL AND 场景提及药品 != '[]' "
            "GROUP BY t.drug ORDER BY 场景数 DESC LIMIT 20",
            True,
            "无条件场景提及药品 UNNEST"
        ),
        (
            "SELECT t.drug AS 顾客点名药品, COUNT(DISTINCT data.场景ID) AS 场景数 "
            "FROM data, LATERAL UNNEST(string_split(TRIM(顾客点名药品, '[]'), ',')) AS t(drug) "
            "WHERE 顾客点名药品 IS NOT NULL AND 顾客点名药品 != '[]' "
            "GROUP BY t.drug ORDER BY 场景数 DESC LIMIT 20",
            True,
            "无条件顾客点名药品 UNNEST"
        ),
        (
            "SELECT t.drug AS 订单药品, COUNT(DISTINCT data.场景ID) AS 场景数 "
            "FROM data, LATERAL UNNEST(string_split(TRIM(订单药品, '[]'), ',')) AS t(drug) "
            "WHERE 订单药品 IS NOT NULL AND 订单药品 != '[]' "
            "GROUP BY t.drug ORDER BY 场景数 DESC LIMIT 20",
            True,
            "无条件订单药品 UNNEST"
        ),
        (
            "SELECT t.drug AS 场景提及药品, COUNT(DISTINCT data.场景ID) AS 场景数 "
            "FROM data, LATERAL UNNEST(string_split(TRIM(场景提及药品, '[]'), ',')) AS t(drug) "
            "WHERE 场景提及药品 IS NOT NULL AND 场景提及药品 != '[]' AND 疾病名称 LIKE '%高血压%' "
            "GROUP BY t.drug ORDER BY 场景数 DESC LIMIT 20",
            True,
            "带疾病条件 UNNEST"
        ),
        (
            "SELECT t.drug AS 场景提及药品, "
            "COUNT(DISTINCT CASE WHEN 交易是否达成='是' THEN data.场景ID END) AS 成交场景数 "
            "FROM data, LATERAL UNNEST(string_split(TRIM(场景提及药品, '[]'), ',')) AS t(drug) "
            "WHERE 场景提及药品 IS NOT NULL AND 场景提及药品 != '[]' "
            "GROUP BY t.drug ORDER BY 成交场景数 DESC LIMIT 20",
            True,
            "成交场景数 UNNEST"
        ),
        (
            "SELECT 疾病名称, COUNT(DISTINCT 场景ID) AS 场景数 "
            "FROM data WHERE 疾病名称 LIKE '%高血压%' "
            "GROUP BY 疾病名称 ORDER BY 场景数 DESC LIMIT 10",
            False,
            "普通疾病查询（不应改写）"
        ),
        (
            "SELECT 月份, COUNT(DISTINCT 场景ID) AS 场景数 "
            "FROM data GROUP BY 月份 ORDER BY 月份 DESC LIMIT 12",
            False,
            "时间趋势查询（不应改写）"
        ),
    ]

    for sql, should_rewrite, desc in cases:
        rewritten = e._rewrite_drug_unnest_query(sql)
        is_rewritten = (rewritten != sql)
        check(desc, is_rewritten == should_rewrite,
              f"期望改写={should_rewrite}, 实际={is_rewritten}")


def test_execution(e: DuckDbEngine):
    """测试执行正确性"""
    print("\n=== 测试2: 执行正确性 ===")

    tests = [
        (
            "SELECT t.drug AS 场景提及药品, COUNT(DISTINCT data.场景ID) AS 场景数 "
            "FROM data, LATERAL UNNEST(string_split(TRIM(场景提及药品, '[]'), ',')) AS t(drug) "
            "WHERE 场景提及药品 IS NOT NULL AND 场景提及药品 != '[]' "
            "GROUP BY t.drug ORDER BY 场景数 DESC LIMIT 20",
            "无条件场景提及药品 TOP20"
        ),
        (
            "SELECT t.drug AS 顾客点名药品, COUNT(DISTINCT data.场景ID) AS 场景数 "
            "FROM data, LATERAL UNNEST(string_split(TRIM(顾客点名药品, '[]'), ',')) AS t(drug) "
            "WHERE 顾客点名药品 IS NOT NULL AND 顾客点名药品 != '[]' "
            "GROUP BY t.drug ORDER BY 场景数 DESC LIMIT 10",
            "无条件顾客点名药品 TOP10"
        ),
        (
            "SELECT t.drug AS 场景提及药品, COUNT(DISTINCT data.场景ID) AS 场景数 "
            "FROM data, LATERAL UNNEST(string_split(TRIM(场景提及药品, '[]'), ',')) AS t(drug) "
            "WHERE 场景提及药品 IS NOT NULL AND 场景提及药品 != '[]' AND 疾病名称 LIKE '%高血压%' "
            "GROUP BY t.drug ORDER BY 场景数 DESC LIMIT 10",
            "高血压场景提及药品 TOP10"
        ),
        (
            "SELECT t.drug AS 场景提及药品, "
            "COUNT(DISTINCT CASE WHEN 交易是否达成='是' THEN data.场景ID END) AS 成交场景数 "
            "FROM data, LATERAL UNNEST(string_split(TRIM(场景提及药品, '[]'), ',')) AS t(drug) "
            "WHERE 场景提及药品 IS NOT NULL AND 场景提及药品 != '[]' "
            "GROUP BY t.drug ORDER BY 成交场景数 DESC LIMIT 20",
            "成交场景数 TOP20"
        ),
        (
            "SELECT t.drug AS 订单药品, COUNT(DISTINCT data.场景ID) AS 场景数 "
            "FROM data, LATERAL UNNEST(string_split(TRIM(订单药品, '[]'), ',')) AS t(drug) "
            "WHERE 订单药品 IS NOT NULL AND 订单药品 != '[]' AND 城市 LIKE '%上海%' "
            "GROUP BY t.drug ORDER BY 场景数 DESC LIMIT 10",
            "订单药品 + 城市条件 TOP10"
        ),
    ]

    for sql, desc in tests:
        try:
            start = time.time()
            res = e.execute(sql)
            elapsed = (time.time() - start) * 1000
            ok = res.get("success", False) and len(res.get("rows", [])) > 0
            check(desc, ok, f"fail={res.get('error')}, elapsed={elapsed:.0f}ms")
            if ok:
                top = res["rows"][0]
                print(f"      {elapsed:.0f}ms TOP1: {top}")
        except Exception as ex:
            check(desc, False, str(ex))


def test_non_unnest_queries_unaffected(e: DuckDbEngine):
    """测试非 UNNEST 查询不受影响"""
    print("\n=== 测试3: 非 UNNEST 查询不受影响 ===")

    tests = [
        ("SELECT 疾病名称, COUNT(DISTINCT 场景ID) AS 场景数 "
         "FROM data WHERE 疾病名称 LIKE '%高血压%' "
         "GROUP BY 疾病名称 ORDER BY 场景数 DESC LIMIT 10",
         "疾病分布查询"),
        ("SELECT 城市, COUNT(DISTINCT 场景ID) AS 场景数 "
         "FROM data GROUP BY 城市 ORDER BY 场景数 DESC LIMIT 10",
         "城市分布查询"),
        ("""SELECT strftime(ydate, '%Y-%m') AS 月份, COUNT(DISTINCT 场景ID) AS 场景数 
FROM data GROUP BY 月份 ORDER BY 月份 DESC LIMIT 12""",
         "月度趋势查询"),
    ]

    for sql, desc in tests:
        try:
            res = e.execute(sql)
        except Exception as ex:
            check(desc, False, str(ex))
            continue
        # 验证不是 UNNEST 改写的结果（没有药品名列）
        rows = res.get("rows", [])
        has_drug_col = any("药品名" in r for r in rows) if rows else False
        check(desc, res.get("success", False) and not has_drug_col,
              f"success={res.get('success')}, has_drug_col={has_drug_col}")


def test_rewrite_end_to_end(e: DuckDbEngine):
    """端到端验证改写链路完整"""
    print("\n=== 测试4: 端到端改写链路 ===")

    # 验证 execute() 确实触发了 rewrite
    # 通过日志模式检查（print输出）
    sql = ("SELECT t.drug AS 场景提及药品, COUNT(DISTINCT data.场景ID) AS 场景数 "
           "FROM data, LATERAL UNNEST(string_split(TRIM(场景提及药品, '[]'), ',')) AS t(drug) "
           "WHERE 场景提及药品 IS NOT NULL AND 场景提及药品 != '[]' "
           "GROUP BY t.drug ORDER BY 场景数 DESC LIMIT 5")

    res = e.execute(sql)
    rows = res.get("rows", [])

    check("结果非空", len(rows) > 0, f"rows={len(rows)}")
    check("结果含药品名列", all("场景提及药品" in r for r in rows) if rows else True,
          "第一行: {rows[0] if rows else 'empty'}")

    # 验证值都是干净的（没有引号）
    if rows:
        all_clean = all(not r["场景提及药品"].startswith('"') for r in rows)
        check("药品名已去引号", all_clean,
              f"有引号的: {[r['场景提及药品'] for r in rows if r['场景提及药品'].startswith('"')]}")


if __name__ == "__main__":
    print("=" * 60)
    print("P2-1 UNNEST 改写单元测试")
    print("=" * 60)

    e = DuckDbEngine()
    e.db_file = '/tmp/star-query-bench.duckdb'
    e._db_file = '/tmp/star-query-bench.duckdb'
    e.load_data()

    test_rewrite_detection(e)
    test_execution(e)
    test_non_unnest_queries_unaffected(e)
    test_rewrite_end_to_end(e)

    print(f"\n{'=' * 40}")
    print(f"结果: {PASS} 通过, {FAIL} 失败")
    print(f"{'=' * 40}")
    sys.exit(0 if FAIL == 0 else 1)
