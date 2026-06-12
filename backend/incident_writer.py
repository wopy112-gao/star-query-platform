"""星宝数据平台 — 反馈事件写入器

在校验失败、用户踩等场景中，将异常事件结构化写入
workspace/feedback_review/ 目录，供智能体在 heartbeat/session
中扫到后分析根因并输出迭代方案。

文件格式：inc_{timestamp}_{env}_{seq}.json
示例：    inc_20260526_114200_test_001.json
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

# 反馈事件队列目录（支持环境变量覆盖，方便测试/生产环境分离）
_FEEDBACK_DIR_ENV = os.environ.get("FEEDBACK_REVIEW_DIR")
_FEEDBACK_DIR = (
    Path(_FEEDBACK_DIR_ENV)
    if _FEEDBACK_DIR_ENV
    else Path(os.path.expanduser("~/.lightclaw/workspace/feedback_review"))
)
_FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)

# 环境标识
_ENV_TAG = "test" if "test" in (os.getcwd() or "") else "prod"


def _get_seq() -> int:
    """获取当前序列号"""
    prefix = f"inc_"
    existing = list(_FEEDBACK_DIR.glob(f"inc_*_{_ENV_TAG}_*.json"))
    if not existing:
        return 1
    seqs = []
    for f in existing:
        try:
            seq = int(f.stem.rsplit("_", 1)[-1])
            seqs.append(seq)
        except (ValueError, IndexError):
            continue
    return (max(seqs) if seqs else 0) + 1


def write_incident(
    inc_type: str,           # "validation_fail" | "user_dislike"
    question: str,           # 原始问题
    sql: str = "",           # 生成的 SQL
    error: str = "",         # 错误信息
    warnings: list[str] | None = None,  # 质量门禁警告
    intent_info: dict | None = None,    # 意图拆解结果
    history_id: str = "",    # 历史记录 ID（踩反馈时）
    feedback_comment: str = "",  # 用户备注（踩反馈时）
):
    """写入一条反馈事件

    所有参数均已校验，确保写入合法 JSON。
    """
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    seq = _get_seq()
    filename = f"inc_{ts}_{_ENV_TAG}_{seq:03d}.json"
    filepath = _FEEDBACK_DIR / filename

    record = {
        "incident_id": f"inc_{ts}_{_ENV_TAG}_{seq:03d}",
        "type": inc_type,
        "env": _ENV_TAG,
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": int(time.time()),
        "question": question,
        "sql": sql,
        "error": error,
        "warnings": warnings or [],
        "intent_info": intent_info,
        "history_id": history_id,
        "feedback_comment": feedback_comment,
        "status": "pending",   # pending / analyzing / resolved
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

    print(f"[Incident] 已写入 JSON: {filepath}")

    # === 双写：同步写入 SQLite（管理后台数据源）===
    _write_to_sqlite(record)

    return record["incident_id"]


def _write_to_sqlite(record: dict):
    """将事件记录同步写入 SQLite incidents 表"""
    try:
        from admin_store import import_incident
        import_incident(record)
        print(f"[Incident] 已写入 SQLite: {record.get('incident_id')}")
    except Exception as e:
        print(f"[Incident] SQLite 写入失败（不影响 JSON）: {e}")
