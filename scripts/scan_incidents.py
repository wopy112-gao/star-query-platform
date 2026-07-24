#!/usr/bin/env python3
"""
星宝自愈系统 — cron 入口脚本
============================================================
职责：

  1. 从 SQLite incidents 表查找 status='pending' 的事件
  2. 有事件 → 调 incident_analyzer 分析分组 → 生成 proposal JSON
  3. 调 fix_applier 执行高置信度修复（备份 → 应用 → 回归 → 确认/回滚）
  4. 无事件 → 静默退出（0 token 消耗）

用法：
  python3 scripts/scan_incidents.py              # 默认走 SQLite 主线
  python3 scripts/scan_incidents.py --json       # fallback：走 JSON 目录
  python3 scripts/scan_incidents.py --dry-run    # 仅分析不执行

cron 配置（每 30s 执行一次）：
  * * * * * for i in 0 30; do sleep $i; cd /path/to/star-query && python3 scripts/scan_incidents.py; done
============================================================
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime, date

# ============================================================
# 配置（必须在 import incident_analyzer 之前设置环境变量）
# ============================================================

# 默认走 SQLite 主线
SOURCE = "sqlite"

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
BACKEND_DIR = PROJECT_DIR / "backend"

# fix_proposals 目录（测试环境专有，不跟正式环境共享）
_PROPOSAL_DIR = PROJECT_DIR / "fix_proposals"
_PROPOSAL_DIR.mkdir(parents=True, exist_ok=True)
os.environ["FIX_PROPOSALS_DIR"] = str(_PROPOSAL_DIR)
os.environ["FEEDBACK_REVIEW_DIR"] = str(
    Path.home() / ".lightclaw/workspace/feedback_review"
)

sys.path.insert(0, str(BACKEND_DIR))

# ============================================================
# 增量评估（P4：量化自愈系统效果，每天首次运行时统计）
# ============================================================

_METRICS_FLAG = "/tmp/star-sync-metrics-date"


def _report_metrics():
    """统计自愈系统核心指标，每天仅输出一次
    
    指标：
    - 自愈覆盖率（heal_rate）：resolved / total，目标 >60%
    - 自动修复率（auto_fix_rate）：resolved / (resolved+failed)，目标 >40%
    - 回滚率（rollback_rate）：rolled_back / resolved，目标 <20%
    
    数据量 <100 条时不输出，避免小样本噪音。
    """
    # 检查今天是否已统计过
    today = date.today().isoformat()
    try:
        if Path(_METRICS_FLAG).exists() and Path(_METRICS_FLAG).read_text().strip() == today:
            return
    except Exception:
        pass

    db_path = PROJECT_DIR / "star-query-history.db"
    if not db_path.exists():
        return

    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))

        total = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
        if total < 100:
            conn.close()
            # 数据量小时不输出，但更新标记避免每次都查
            Path(_METRICS_FLAG).write_text(today)
            return

        resolved = conn.execute(
            "SELECT COUNT(*) FROM incidents WHERE status='resolved'"
        ).fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM incidents WHERE status='failed'"
        ).fetchone()[0]
        rolled_back = conn.execute(
            "SELECT COUNT(*) FROM incidents WHERE status='rolled_back'"
        ).fetchone()[0]

        conn.close()

        heal_rate = resolved / total if total > 0 else 0
        auto_fix_rate = resolved / (resolved + failed) if (resolved + failed) > 0 else 0
        rollback_rate = rolled_back / resolved if resolved > 0 else 0

        print(f"[评估] 📊 自愈系统指标（{total} 条 incidents）")
        print(f"  ├─ 自愈覆盖率: {heal_rate:.1%}   目标 >60%  {'✅' if heal_rate > 0.6 else '❌'}")
        print(f"  ├─ 自动修复率: {auto_fix_rate:.1%}  目标 >40%  {'✅' if auto_fix_rate > 0.4 else '❌'}")
        print(f"  └─ 回滚率:    {rollback_rate:.1%}   目标 <20%  {'✅' if rollback_rate < 0.2 else '❌'}")

        # 记录统计日期
        Path(_METRICS_FLAG).write_text(today)

    except Exception as e:
        print(f"[评估] 统计失败: {e}")


def main():
    # ---- 增量评估（每天首次运行输出指标） ----
    _report_metrics()

    # ---- 参数解析 ----
    dry_run = "--dry-run" in sys.argv
    if "--json" in sys.argv:
        source = "json"
    else:
        source = SOURCE

    print(f"[Scan] 开始扫描 incidents（来源: {source}）")

    # ---- Step 1: 扫描 pending 事件 ----
    from incident_analyzer import scan_pending, scan_pending_from_sqlite, group_similar

    if source == "sqlite":
        # 从 SQLite incidents 表读取
        pending = scan_pending_from_sqlite()
    else:
        # fallback：从 JSON 目录读取
        pending = scan_pending()

    if not pending:
        print("[Scan] 无 pending 事件，静默退出")
        return

    print(f"[Scan] 发现 {len(pending)} 条待处理事件")

    # ---- Step 2: 分组分析 ----
    from incident_analyzer import generate_proposal, save_proposal, mark_analyzed_sqlite

    groups = group_similar(pending)
    print(f"[Scan] 分组后共 {len(groups)} 个错误模式")

    proposals = []
    for group in groups:
        proposal = generate_proposal(group)
        if not proposal:
            continue

        save_proposal(proposal)
        proposals.append(proposal)

        # 标记为已分析（SQLite → 更新 status='analyzed' + 写入 root_cause/fix_proposal）
        for inc in group:
            mark_analyzed_sqlite(
                inc.get("incident_id", ""),
                root_cause=proposal.get("root_cause", ""),
                fix_proposal=proposal.get("fix_proposal", ""),
            )

        # 将分析结果同步到 JSON 文件（保持双写一致性）
        try:
            from incident_analyzer import mark_analyzed
            for inc in group:
                mark_analyzed(inc.get("incident_id", ""))
        except Exception:
            pass  # JSON 文件的标记是辅助性的，不阻塞流程

    if not proposals:
        print("[Scan] 分析后未生成有效方案")
        return

    print(f"[Scan] 生成了 {len(proposals)} 个修复方案")
    for p in proposals:
        pid = p.get("proposal_id", "?")
        rc = p.get("root_cause", "")[:60]
        conf = p.get("confidence", "?")
        print(f"  ├─ {pid} [{conf}] → {rc}")

    # ---- Step 3: 走 fix_applier 执行（非 dry-run） ----
    if dry_run:
        print("[Scan] DRY RUN 模式，跳过执行")
        return

    from fix_applier import FixApplier
    applier = FixApplier()
    notification = applier.run()
    print()
    print("=" * 50)
    print(notification)
    print("=" * 50)


if __name__ == "__main__":
    main()
