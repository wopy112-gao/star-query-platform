#!/usr/bin/env python3
"""星宝语料数据每日简报生成器

用法: python daily_briefing.py
依赖: requests (pip install requests)

输出: output/daily_briefing_YYYY-MM-DD.md
"""

import json
import sys
from pathlib import Path
from datetime import date, datetime
from urllib.request import Request, urlopen
from urllib.error import URLError

# ---- 配置 ----
API_BASE = "http://localhost:8000"
USERNAME = "admin"
PASSWORD = "admin888"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

# ---- 工具函数 ----
def api_post(path, data):
    req = Request(f"{API_BASE}{path}", method="POST",
                  data=json.dumps(data).encode("utf-8"),
                  headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))

def api_get(path, token):
    req = Request(f"{API_BASE}{path}", method="GET",
                  headers={"Authorization": f"Bearer {token}"})
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))

# ---- 主逻辑 ----
def main():
    today = date.today()
    print(f"[简报] {today} — 开始生成")

    # 1. 登录
    login_resp = api_post("/api/auth/login", {
        "username": USERNAME,
        "password": PASSWORD
    })
    token = login_resp.get("token", "")
    if not token:
        print("[简报] ❌ 登录失败")
        sys.exit(1)
    print("[简报] ✅ 登录成功")

    # 2. 获取洞察数据
    insights = api_get("/api/insights", token)
    total = insights.get("total", {})
    trend = insights.get("trend", [])
    top_diseases = insights.get("top_diseases", [])
    top_provinces = insights.get("top_provinces", [])
    alerts = insights.get("alerts", [])
    date_range = insights.get("date_range", {})

    print(f"[简报] ✅ 数据获取完成: {total.get('total_scenes', 0):,} 场景")

    # 3. 计算趋势变化
    # 最近有数据的日期
    last_3_days = [d for d in trend if d["count"] > 0][-3:] if trend else []
    trend_direction = ""
    if len(last_3_days) >= 2:
        prev = last_3_days[-2]["count"]
        curr = last_3_days[-1]["count"]
        if curr > prev:
            pct = ((curr - prev) / prev * 100) if prev > 0 else 0
            trend_direction = f"📈 +{pct:.1f}%"
        elif curr < prev:
            pct = ((prev - curr) / prev * 100) if prev > 0 else 0
            trend_direction = f"📉 -{pct:.1f}%"
        else:
            trend_direction = "➡️ 持平"

    # 每周同环比（取最近完整周的数据）
    week_count = total.get("week_scenes", 0)

    # 4. 组装简报
    lines = []
    lines.append(f"# 🏥 星宝语料数据日报 — {today}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 核心指标
    lines.append("## 📊 核心指标")
    lines.append("")
    tc = total.get("total_scenes", 0)
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|:----|:----:|")
    lines.append(f"| 累计场景数 | {tc:,} |")
    lines.append(f"| 本周场景数 | {week_count:,} |")
    lines.append(f"| 成交率 | {total.get('close_rate', 0):.1f}% |")
    lines.append(f"| 问症率 | {total.get('inquiry_rate', 0):.1f}% |")
    lines.append(f"| 联合用药率 | {total.get('combo_rate', 0):.1f}% |")
    lines.append(f"| 数据范围 | {date_range.get('min_date', '-')} ~ {date_range.get('max_date', '-')} |")

    lines.append("")
    lines.append("---")
    lines.append("")

    # 趋势
    lines.append("## 📈 近7日场景趋势")
    lines.append("")
    lines.append("| 日期 | 场景数 |")
    lines.append("|:----|:-----:|")
    for d in trend[-7:]:
        bar = "█" * min(d["count"] // 1000, 30) if d["count"] > 0 else ""
        lines.append(f"| {d['date']} | {d['count']:>6,} {'  ' + bar if bar else ''} |")
    lines.append("")
    lines.append(f"趋势方向: {trend_direction}")
    lines.append("")

    # 疾病TOP5
    lines.append("---")
    lines.append("## 🏥 疾病TOP5")
    lines.append("")
    lines.append("| # | 疾病 | 场景数 | 占比 |")
    lines.append("|:-:|:----|:-----:|:---:|")
    for i, d in enumerate(top_diseases[:5], 1):
        name = d["name"].split("-")[-1] if "-" in d["name"] else d["name"]
        pct = d["count"] / tc * 100 if tc > 0 else 0
        bar = "█" * int(pct / 3)
        lines.append(f"| {i} | {name} | {d['count']:>6,} | {pct:.1f}% {bar} |")
    lines.append("")

    # 省份TOP5
    lines.append("---")
    lines.append("## 📍 省份TOP5")
    lines.append("")
    lines.append("| # | 省份 | 场景数 | 占比 |")
    lines.append("|:-:|:----|:-----:|:---:|")
    for i, d in enumerate(top_provinces[:5], 1):
        pct = d["count"] / tc * 100 if tc > 0 else 0
        bar = "█" * int(pct / 3)
        lines.append(f"| {i} | {d['name']} | {d['count']:>6,} | {pct:.1f}% {bar} |")
    lines.append("")

    # 异常告警
    if alerts:
        lines.append("---")
        lines.append("## ⚠️ 异常告警")
        lines.append("")
        for a in alerts:
            level_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(a.get("level", "low"), "⚪")
            lines.append(f"- {level_icon} {a['message']}")
        lines.append("")

    # 页脚
    lines.append("---")
    lines.append(f"_自动生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 系统: 星宝语料场景查询系统 v2.0_")

    # 5. 写入文件
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"daily_briefing_{today}.md"
    filepath = OUTPUT_DIR / filename
    filepath.write_text("\n".join(lines), encoding="utf-8")

    print(f"[简报] ✅ 文件已生成: {filepath}")
    print(f"[简报] ✅ 简报长度: {len(lines)} 行")
    return str(filepath)


if __name__ == "__main__":
    main()
