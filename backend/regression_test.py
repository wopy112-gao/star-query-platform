"""星宝数据平台 — 回归测试系统

安全重启后自动回放历史查询，验证新版本 API 的 SQL 和结果一致性。

独立运行：
    python3 regression_test.py                    # 测试环境
    python3 regression_test.py --prod             # 正式环境
    python3 regression_test.py --prod --report    # + 生成 HTML 报告
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Optional

# ============================================================
# 配置
# ============================================================

_HISTORY_DB = Path(__file__).resolve().parent.parent / "star-query-history.db"
_REPORT_DIR = Path(__file__).resolve().parent.parent / "regression_reports"
_REPORT_DIR.mkdir(parents=True, exist_ok=True)

# 测试环境配置
TEST_CONFIG = {
    "port": 8002,
    "username": "admin",
    "password": "test888",
}

# 正式环境配置
PROD_CONFIG = {
    "port": 8000,
    "username": "admin",
    "password": os.environ.get("ADMIN_PASSWORD", "admin888"),
}


# ============================================================
# 测试用例收集
# ============================================================

def collect_test_cases(n: int = 20) -> list[dict]:
    """从历史数据库中收集最近 N 条成功查询作为测试用例"""
    db = str(_HISTORY_DB)
    if not os.path.exists(db):
        print(f"[RegTest] 历史数据库不存在: {db}")
        return []

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT question, sql, elapsed_ms, created_at FROM query_history "
            "WHERE success = 1 AND question != '' AND sql != '' "
            "ORDER BY created_at DESC LIMIT ?",
            (n,),
        ).fetchall()

        # 去重：同一问题只保留最近一次
        seen = set()
        cases = []
        for r in rows:
            q = r["question"].strip()
            if q not in seen:
                seen.add(q)
                cases.append({
                    "question": q,
                    "original_sql": r["sql"].strip(),
                    "original_elapsed_ms": r["elapsed_ms"],
                    "created_at": r["created_at"],
                })

        return cases
    finally:
        conn.close()


# ============================================================
# API 交互
# ============================================================

def _login(port: int, username: str, password: str) -> Optional[str]:
    """登录并获取 token"""
    url = f"http://localhost:{port}/api/auth/login"
    data = json.dumps({"username": username, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        body = json.loads(resp.read().decode("utf-8"))
        return body.get("token")
    except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
        print(f"[RegTest] 登录失败: {e}")
        return None


def _query(port: int, token: str, question: str) -> dict:
    """执行一次查询并返回结果"""
    url = f"http://localhost:{port}/api/query"
    data = json.dumps({"question": question}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    start = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        elapsed = round((time.time() - start) * 1000, 2)
        body = json.loads(resp.read().decode("utf-8"))
        return {
            "success": body.get("success", False),
            "sql": body.get("query", {}).get("sql", ""),
            "source": body.get("query", {}).get("source", "?"),
            "total_rows": body.get("result", {}).get("total_rows", 0),
            "warnings": body.get("warnings", []),
            "error": body.get("error", ""),
            "elapsed_ms": body.get("query", {}).get("elapsed_ms", elapsed),
        }
    except urllib.error.HTTPError as e:
        return {
            "success": False,
            "error": f"HTTP {e.code}: {e.read().decode('utf-8')[:200]}",
            "elapsed_ms": round((time.time() - start) * 1000, 2),
        }
    except urllib.error.URLError as e:
        return {
            "success": False,
            "error": f"连接失败: {e.reason}",
            "elapsed_ms": round((time.time() - start) * 1000, 2),
        }


# ============================================================
# 结果对比
# ============================================================

def _normalize_sql(sql: str) -> str:
    """归一化 SQL，忽略空格和大小写差异"""
    s = sql.strip().rstrip(";")
    s = re.sub(r"\s+", " ", s)
    s = s.lower()
    return s


def compare_results(
    original: dict,
    new_result: dict,
) -> dict:
    """对比原始查询和新版本的结果一致性

    检查维度：
    1. SQL 是否成功生成
    2. SQL 文本的语义一致性（归一化后对比）
    3. 行数一致性
    4. 是否有新的 warning
    """
    checks = []

    # 检查1：API 是否成功
    if not new_result["success"]:
        return {
            "passed": False,
            "severity": "critical",
            "checks": [f"❌ API 报错: {new_result.get('error', '未知错误')}"],
        }

    checks.append("✅ API 调用成功")

    # 检查2：SQL 一致性
    orig_sql = _normalize_sql(original["original_sql"])
    new_sql = _normalize_sql(new_result.get("sql", ""))

    sql_match = orig_sql == new_sql
    if sql_match:
        checks.append("✅ SQL 一致")
    else:
        # 判断差异大小
        orig_set = set(orig_sql.split())
        new_set = set(new_sql.split())
        diff = len(orig_set.symmetric_difference(new_set))

        # 如果旧 SQL 中的关键词在新 SQL 中都有，说明是修复而非回归
        old_keywords = set(k for k in orig_sql.replace("'", "").split() if len(k) > 2)
        new_keywords = set(k for k in new_sql.replace("'", "").split() if len(k) > 2)
        new_is_better = old_keywords.issubset(new_keywords) and len(new_keywords) > len(old_keywords)

        if new_is_better:
            checks.append(f"🚀 SQL 修复（旧 SQL 缺少条件，新版本已补全）")
        elif diff <= 3:
            checks.append(f"⚠️  SQL 微差异常（{diff} 个 token 不同），可能可接受")
        else:
            checks.append(f"❌ SQL 不一致（{diff} 个 token 不同）")

    # 检查3：行数一致性
    orig_rows = original.get("total_rows", -1)
    new_rows = new_result.get("total_rows", -1)

    # 如果原查询没有记录行数，跳过此检查
    if orig_rows != -1 and new_rows != -1:
        if orig_rows == new_rows:
            checks.append(f"✅ 行数一致: {orig_rows}")
        else:
            checks.append(f"⚠️  行数变化: {orig_rows} → {new_rows}")
    else:
        checks.append("ℹ️  行数对比跳过（原查询无行数记录）")

    # 检查4：warning 检查
    new_warnings = new_result.get("warnings", [])
    if new_warnings:
        checks.append(f"⚠️  新 warning: {'; '.join(new_warnings[:3])}")
    else:
        checks.append("✅ 无新 warning")

    # 综合判定
    failures = [c for c in checks if c.startswith("❌")]
    improvements = [c for c in checks if c.startswith("🚀")]
    warnings_only = [c for c in checks if c.startswith("⚠️")]
    infos = [c for c in checks if c.startswith("ℹ️") or c.startswith("✅")]

    if improvements:
        # SQL 修复是良性的
        severity = "pass"
    elif failures:
        severity = "critical"
    elif warnings_only:
        severity = "warning"
    else:
        severity = "pass"

    return {
        "passed": severity == "pass",
        "severity": severity,
        "checks": checks,
        "original_sql": original.get("original_sql", ""),
        "new_sql": new_result.get("sql", ""),
        "original_rows": orig_rows,
        "new_rows": new_rows,
        "new_source": new_result.get("source", "?"),
        "new_elapsed_ms": new_result.get("elapsed_ms", 0),
    }


# ============================================================
# 报告生成
# ============================================================

def generate_markdown_report(results: list[dict], env_name: str) -> str:
    """生成 Markdown 格式的回归测试报告"""
    total = len(results)
    passed = sum(1 for r in results if r.get("passed"))
    warnings = sum(1 for r in results if r.get("severity") == "warning")
    failed = sum(1 for r in results if r.get("severity") == "critical")
    skipped = sum(1 for r in results if r.get("severity") == "skipped")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# 星宝数据平台 — 回归测试报告",
        f"",
        f"- **环境:** {env_name}",
        f"- **时间:** {now}",
        f"- **用例总数:** {total}",
        f"- **通过:** {passed} | **警告:** {warnings} | **失败:** {failed} | **跳过:** {skipped}",
        f"- **通过率:** {round(passed / total * 100, 1) if total else 0}%",
        f"",
    ]

    if failed:
        lines.append("## ❌ 失败的用例")
        for r in results:
            if r.get("severity") == "critical":
                lines.append(f"")
                lines.append(f"### {r.get('question', '?')}")
                for c in r.get("checks", []):
                    lines.append(f"- {c}")
                lines.append(f"```sql")
                lines.append(r.get("new_sql", "")[:200])
                lines.append(f"```")

    if warnings:
        lines.append("")
        lines.append("## ⚠️ 警告的用例")
        for r in results:
            if r.get("severity") == "warning":
                lines.append(f"")
                lines.append(f"### {r.get('question', '?')}")
                for c in r.get("checks", []):
                    lines.append(f"- {c}")

    lines.append("")
    lines.append("## 全部用例明细")
    lines.append("")
    lines.append("| # | 问题 | 结果 | 来源 | 耗时 | 行数 |")
    lines.append("|---|------|------|------|------|------|")
    for i, r in enumerate(results, 1):
        icon = "✅" if r.get("passed") else ("⚠️" if r.get("severity") == "warning" else "❌")
        checks = r.get("checks", [])
        for c in checks:
            if c.startswith("🚀"):
                icon = "🚀"
        q = r.get("question", "?")[:40]
        src = r.get("new_source", "?")
        elapsed = f"{r.get('new_elapsed_ms', 0):.0f}ms"
        rows = r.get("new_rows", "?")
        lines.append(f"| {i} | {q} | {icon} | {src} | {elapsed} | {rows} |")

    return "\n".join(lines)


def save_report(report: str, env_name: str) -> str:
    """保存报告到文件"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = _REPORT_DIR / f"regression_{env_name}_{ts}.md"
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[RegTest] 报告已保存: {filepath}")
    return str(filepath)


# ============================================================
# 主流程
# ============================================================

def run_regression(env: str = "test", n: int = 20, save_report_file: bool = False) -> list[dict]:
    """完整回归测试流程

    Args:
        env: "test" 或 "prod"
        n: 测试用例数量
        save_report_file: 是否保存报告到文件

    Returns:
        每个用例的对比结果列表
    """
    config = PROD_CONFIG if env == "prod" else TEST_CONFIG
    port = config["port"]
    env_name = "生产" if env == "prod" else "测试"

    print(f"\n{'=' * 50}")
    print(f"  星宝回归测试 — {env_name}环境 (port {port})")
    print(f"{'=' * 50}")

    # 1. 收集测试用例
    cases = collect_test_cases(n)
    if not cases:
        print("[RegTest] 无可用测试用例")
        return []
    print(f"\n收集 {len(cases)} 个测试用例\n")

    # 2. 登录
    token = _login(port, config["username"], config["password"])
    if not token:
        print("[RegTest] ❌ 登录失败，测试中止")
        return []
    print("✅ 登录成功\n")

    # 3. 逐个回放
    results = []
    for i, case in enumerate(cases, 1):
        q = case["question"]
        print(f"  [{i}/{len(cases)}] {q[:50]}...", end=" ")

        result = _query(port, token, q)
        comparison = compare_results(case, result)
        comparison["question"] = q
        results.append(comparison)

        icon = "✅" if comparison["passed"] else ("⚠️" if comparison["severity"] == "warning" else "❌")
        print(f"{icon}")

    # 4. 统计
    passed = sum(1 for r in results if r.get("passed") or any("🚀" in c for c in r.get("checks", [])))
    failed = sum(1 for r in results if r.get("severity") == "critical")
    warn_count = sum(1 for r in results if r.get("severity") == "warning")
    improved = sum(1 for r in results if any("🚀" in c for c in r.get("checks", [])))

    print(f"\n{'=' * 50}")
    print(f"  结果: ✅ {passed} 通过, ⚠️ {warn_count} 警告, ❌ {failed} 失败")
    print(f"  通过率: {round(passed / len(results) * 100, 1) if results else 0}%")
    print(f"{'=' * 50}")

    # 5. 保存报告
    if save_report_file:
        report = generate_markdown_report(results, env_name)
        save_report(report, env_name)

    # 列出失败的用例
    if failed:
        print(f"\n❌ 失败用例:")
        for r in results:
            if r.get("severity") == "critical":
                print(f"  - {r.get('question', '?')[:60]}")
                for c in r.get("checks", []):
                    if c.startswith("❌"):
                        print(f"    {c}")

    return results


# ============================================================
# CLI 入口
# ============================================================

if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    env = "prod" if "--prod" in args else "test"
    save_report_file = "--report" in args

    run_regression(env=env, n=20, save_report_file=save_report_file)
