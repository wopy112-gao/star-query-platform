"""
星宝数据平台 — 批量重命名用户脚本

执行步骤：
1. 更新 .env 文件中的 USERS 字段（key 和 value 同步改）
2. 更新 SQLite 中 users 表（主键）
3. 更新 password_override 表（主键）
4. 更新 login_log 表
5. 更新 query_history 表

用法：
    python3 scripts/rename_users.py                 # 正式环境（port 8000 的 .env）
    python3 scripts/rename_users.py --test           # 测试环境（port 8002 的 .env）
    python3 scripts/rename_users.py --dry-run        # 只看不改
"""

import sys
import json
import os
import sqlite3
import re
from pathlib import Path

# ============================================================
# 配置
# ============================================================

# 改名映射：旧用户名 → 新用户名（密码也同步为新用户名）
RENAME_MAP = {
    "sales1": "ella",
    "sales2": "hubo",
    "sales3": "liumd",
    "sales4": "dongjl",
    "user1": "amy",
    "user2": "wim",
    "user3": "chenml",
    "user4": "niuhr",
    "user5": "huangmy",
    "user6": "linyq",
    "user7": "gesl",
}

# ============================================================
# 参数解析
# ============================================================

IS_TEST = "--test" in sys.argv
DRY_RUN = "--dry-run" in sys.argv

if IS_TEST:
    WORKSPACE = Path(__file__).resolve().parent.parent.parent / "star-query-test"
else:
    WORKSPACE = Path(__file__).resolve().parent.parent

ENV_FILE = WORKSPACE / ".env"
DB_FILE = WORKSPACE / "star-query-history.db"


def log(msg):
    tag = "[DRY-RUN]" if DRY_RUN else "[EXEC]"
    env_tag = "[TEST]" if IS_TEST else "[PROD]"
    print(f"{tag}{env_tag} {msg}")


# ============================================================
# Step 1: 更新 .env
# ============================================================

def update_env():
    """更新 USERS 字段中的 key"""
    if not ENV_FILE.exists():
        log(f"⚠️ .env 文件不存在: {ENV_FILE}")
        return False

    content = ENV_FILE.read_text(encoding="utf-8")

    # 提取 USERS= 后的 JSON
    match = re.search(r'^USERS=(.+)$', content, re.MULTILINE)
    if not match:
        log("⚠️ .env 中未找到 USERS 字段")
        return False

    old_json_str = match.group(1)
    try:
        users_dict = json.loads(old_json_str)
    except json.JSONDecodeError as e:
        log(f"⚠️ USERS JSON 解析失败: {e}")
        return False

    log(f"当前 USERS 中的用户: {list(users_dict.keys())}")

    # 更新 key（同时更新 value 使其与新用户名一致）
    changed = []
    for old_name, new_name in RENAME_MAP.items():
        if old_name in users_dict:
            old_pwd = users_dict.pop(old_name)
            # 密码也同步为新用户名（用户要求）
            users_dict[new_name] = new_name
            changed.append(f"{old_name} → {new_name}")
        else:
            log(f"  ⚠️ {old_name} 在 USERS 中不存在，跳过")

    if not changed:
        log("没有需要改名的用户")
        return False

    new_json_str = json.dumps(users_dict, ensure_ascii=False, separators=(",", ":"))

    # 替换 .env 中的 USERS 行
    new_content = re.sub(
        r'^USERS=.*$',
        f'USERS={new_json_str}',
        content,
        count=1,
        flags=re.MULTILINE,
    )

    if not DRY_RUN:
        ENV_FILE.write_text(new_content, encoding="utf-8")

    log(f"✅ .env USERS 更新完成: {', '.join(changed)}")
    return True


# ============================================================
# Step 2-5: 更新 SQLite
# ============================================================

def update_sqlite():
    """更新 SQLite 数据库中的用户相关记录"""
    if not DB_FILE.exists():
        log(f"⚠️ SQLite 数据库不存在: {DB_FILE}")
        return False

    conn = sqlite3.connect(str(DB_FILE))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    try:
        # ---- Step 2: users 表 ----
        log("--- 更新 users 表 ---")
        for old_name, new_name in RENAME_MAP.items():
            row = conn.execute(
                "SELECT username FROM users WHERE username = ?", (old_name,)
            ).fetchone()
            if row:
                if not DRY_RUN:
                    # 先删后插（因为 username 是主键，不能直接 UPDATE）
                    row_data = conn.execute(
                        "SELECT * FROM users WHERE username = ?", (old_name,)
                    ).fetchone()
                    if row_data:
                        cols = [d[1] for d in conn.execute("PRAGMA table_info(users)").fetchall()]
                        data_dict = dict(zip(cols, row_data))
                        data_dict["username"] = new_name
                        # 同步更新 display_name 和 note 中的引用
                        if data_dict.get("display_name") == old_name:
                            data_dict["display_name"] = new_name

                        conn.execute("DELETE FROM users WHERE username = ?", (old_name,))
                        placeholders = ",".join(["?" for _ in cols])
                        conn.execute(
                            f"INSERT INTO users ({','.join(cols)}) VALUES ({placeholders})",
                            [data_dict[c] for c in cols],
                        )
                log(f"  ✅ {old_name} → {new_name}")
            else:
                log(f"  ⚠️ {old_name} 在 users 表中不存在，跳过")

        # ---- Step 3: password_override 表 ----
        log("--- 更新 password_override 表 ---")
        for old_name, new_name in RENAME_MAP.items():
            row = conn.execute(
                "SELECT username FROM password_override WHERE username = ?", (old_name,)
            ).fetchone()
            if row:
                if not DRY_RUN:
                    row_data = conn.execute(
                        "SELECT * FROM password_override WHERE username = ?", (old_name,)
                    ).fetchone()
                    if row_data:
                        cols = [d[1] for d in conn.execute("PRAGMA table_info(password_override)").fetchall()]
                        data_dict = dict(zip(cols, row_data))
                        data_dict["username"] = new_name
                        conn.execute("DELETE FROM password_override WHERE username = ?", (old_name,))
                        placeholders = ",".join(["?" for _ in cols])
                        conn.execute(
                            f"INSERT INTO password_override ({','.join(cols)}) VALUES ({placeholders})",
                            [data_dict[c] for c in cols],
                        )
                log(f"  ✅ {old_name} → {new_name}（密码覆盖记录已迁移）")
            else:
                log(f"  - {old_name} 无密码覆盖记录，跳过")

        # ---- Step 4: login_log 表 ----
        log("--- 更新 login_log 表 ---")
        for old_name, new_name in RENAME_MAP.items():
            cnt = conn.execute(
                "SELECT COUNT(*) FROM login_log WHERE username = ?", (old_name,)
            ).fetchone()[0]
            if cnt > 0:
                if not DRY_RUN:
                    conn.execute(
                        "UPDATE login_log SET username = ? WHERE username = ?",
                        (new_name, old_name),
                    )
                log(f"  ✅ {old_name} → {new_name}（{cnt} 条登录记录）")
            else:
                log(f"  - {old_name} 无登录记录，跳过")

        # ---- Step 5: query_history 表 ----
        log("--- 更新 query_history 表 ---")
        for old_name, new_name in RENAME_MAP.items():
            cnt = conn.execute(
                "SELECT COUNT(*) FROM query_history WHERE username = ?", (old_name,)
            ).fetchone()[0]
            if cnt > 0:
                if not DRY_RUN:
                    conn.execute(
                        "UPDATE query_history SET username = ? WHERE username = ?",
                        (new_name, old_name),
                    )
                log(f"  ✅ {old_name} → {new_name}（{cnt} 条查询记录）")
            else:
                log(f"  - {old_name} 无查询记录，跳过")

        if not DRY_RUN:
            conn.commit()
            log("✅ SQLite 所有更改已提交")
        else:
            log("📋 DRY-RUN 模式，未写入任何更改")

        return True

    finally:
        conn.close()


# ============================================================
# 执行
# ============================================================

if __name__ == "__main__":
    log(f"工作目录: {WORKSPACE}")
    log(f"改名映射: {json.dumps(RENAME_MAP, ensure_ascii=False, indent=2)}")
    print()

    ok1 = update_env()
    print()
    ok2 = update_sqlite()
    print()

    if ok1 or ok2:
        log("✅ 重命名脚本执行完毕")
        if DRY_RUN:
            log("💡 去掉 --dry-run 参数即可实际执行")
    else:
        log("❌ 脚本执行失败，请检查")
