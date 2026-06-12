"""星宝数据平台 — 数据迁移脚本

将现有数据迁移到管理后台表：

1. workspace/feedback_review/*.json → incidents 表
2. .env 用户 + password_override 用户 → users 表

用法: python3 backend/migrate_to_admin.py
"""

import json
import os
import sys
from pathlib import Path

# 确保能找到 backend 模块
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import settings
from admin_store import (
    create_user,
    import_incident,
    get_users,
    _get_conn,
    get_incident_stats,
    add_operation_log,
)

# feedback_review 目录
FEEDBACK_DIR = Path(os.path.expanduser("~/.lightclaw/workspace/feedback_review"))


def migrate_users():
    """迁移已有用户到 users 表"""
    print("=" * 50)
    print("迁移用户...")

    # 先获取已存在的用户
    existing = get_users(page=1, limit=1000)
    existing_usernames = {u["username"] for u in existing["items"]}

    created = 0
    skipped = 0

    # 1. admin 用户
    admin_username = settings.ADMIN_USERNAME
    if admin_username not in existing_usernames:
        try:
            create_user(admin_username, role="admin", display_name="管理员")
            print(f"  ✅ 创建 admin 用户: {admin_username}")
            created += 1
        except ValueError as e:
            print(f"  ⚠️  admin 用户创建失败: {e}")
    else:
        skipped += 1

    # 2. 多用户
    for username in settings.USERS:
        if username not in existing_usernames:
            try:
                create_user(username, role="user", display_name=username)
                print(f"  ✅ 创建用户: {username}")
                created += 1
            except ValueError as e:
                print(f"  ⚠️ 创建失败: {e}")
        else:
            skipped += 1

    # 3. password_override 表中存在的额外用户
    conn = _get_conn()
    try:
        override_users = conn.execute(
            "SELECT username FROM password_override"
        ).fetchall()
        for row in override_users:
            u = row["username"]
            if u not in existing_usernames and u != admin_username:
                try:
                    create_user(u, role="user", display_name=u)
                    print(f"  ✅ 创建 override 用户: {u}")
                    created += 1
                except ValueError:
                    pass
    finally:
        conn.close()

    print(f"\n  新建: {created} 人, 已存在(跳过): {skipped} 人")


def migrate_incidents():
    """迁移 feedback_review/*.json 到 incidents 表"""
    print("=" * 50)
    print("迁移反馈事件...")

    if not FEEDBACK_DIR.exists():
        print("  ⚠️  feedback_review 目录不存在，跳过")
        return

    files = sorted(FEEDBACK_DIR.glob("inc_*.json"))
    if not files:
        print("  没有找到事件文件")
        return

    imported = 0
    skipped = 0
    errors = 0

    for f in files:
        try:
            with open(f, "r") as fh:
                incident = json.load(fh)
            ok = import_incident(incident)
            if ok:
                imported += 1
            else:
                skipped += 1
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ❌ 读取失败 {f.name}: {e}")
            errors += 1

    print(f"\n  导入: {imported} 条, 已存在(跳过): {skipped} 条, 错误: {errors} 条")

    # 迁移后统计
    stats = get_incident_stats()
    if stats["total"] > 0:
        print(f"\n  事件库总计: {stats['total']} 条")
        print(f"  待处理: {stats['pending']} 条")
        print(f"  类型分布: {stats['by_type']}")
        print(f"  状态分布: {stats['by_status']}")


def main():
    print("星宝数据平台 — 管理后台数据迁移")
    print("=" * 50)

    migrate_users()
    print()
    migrate_incidents()

    # 记录迁移日志
    add_operation_log(
        username="system",
        action="migration",
        target="all",
        detail="将 feedback_review JSON 和 .env 用户迁移到管理后台表",
    )

    print("\n" + "=" * 50)
    print("✅ 迁移完成!")


if __name__ == "__main__":
    main()
