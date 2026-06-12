"""星宝数据平台 — 管理后台路由

所有接口仅在 admin 用户可访问。
前缀：/api/admin
"""

import csv
import io
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from auth import get_current_user, hash_password, verify_password
from config import settings
from models import (
    DeleteHistoryResponse,
    HistoryItem,
    HistoryResponse,
    LoginLogItem,
    LoginLogsResponse,
)
from admin_store import (
    # Users
    get_users as db_get_users,
    create_user as db_create_user,
    update_user as db_update_user,
    delete_user as db_delete_user,
    toggle_user_status as db_toggle_status,
    get_user as db_get_user,
    get_user_stats as db_get_user_stats,
    add_operation_log,
    # Incidents
    get_incidents as db_get_incidents,
    get_incident as db_get_incident,
    update_incident_status as db_update_status,
    batch_update_incident_status as db_batch_status,
    batch_delete_incidents as db_batch_delete,
    delete_incident as db_delete_incident,
    update_incident_analysis as db_update_analysis,
    update_incident_fix_status as db_update_fix_status,
    get_incident_stats as db_get_incident_stats,
    # Dashboard
    get_dashboard_stats,
)
from history_store import get_history as db_get_history, get_all_history as db_get_all_history
from login_store import get_login_logs as db_get_login_logs
from password_store import delete_override, set_override, get_override
from incident_analyzer import analyze_incident

router = APIRouter(prefix="/api/admin", tags=["管理后台"])


# ============================================================
# 权限校验
# ============================================================

def _require_admin(username: str = Depends(get_current_user)) -> str:
    """要求当前用户为 admin"""
    if username != settings.ADMIN_USERNAME:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅管理员可执行此操作",
        )
    return username


def _get_client_ip(request: Request) -> str:
    """获取客户端真实 IP"""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = request.client
    if client:
        return client.host
    return ""


# ============================================================
# Dashboard — 总览
# ============================================================

@router.get("/dashboard")
def dashboard(admin: str = Depends(_require_admin)):
    """管理后台总览指标"""
    return get_dashboard_stats()


# ============================================================
# Users — 用户管理
# ============================================================

@router.get("/users")
def list_users(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    keyword: str = Query("", description="搜索用户名/展示名"),
    status: str = Query("", description="筛选状态: active/disabled"),
    admin: str = Depends(_require_admin),
):
    """用户列表"""
    return db_get_users(page=page, limit=limit, keyword=keyword, status_filter=status)


@router.get("/users/stats")
def user_stats(admin: str = Depends(_require_admin)):
    """用户使用统计"""
    return db_get_user_stats()


@router.get("/users/{username}")
def get_user_detail(
    username: str,
    admin: str = Depends(_require_admin),
):
    """获取单个用户信息"""
    user = db_get_user(username)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


@router.post("/users")
def create_user(
    req: dict,
    request: Request,
    admin: str = Depends(_require_admin),
):
    """新增用户

    Body: { username, password, role?, display_name?, note? }
    """
    username = req.get("username", "").strip()
    password = req.get("password", "").strip()
    role = req.get("role", "user")
    display_name = req.get("display_name", "")
    note = req.get("note", "")

    if not username:
        raise HTTPException(status_code=400, detail="用户名不能为空")
    if not password:
        raise HTTPException(status_code=400, detail="密码不能为空")
    if role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="角色只能是 admin 或 user")

    # 检查是否已存在
    existing = db_get_user(username)
    if existing:
        raise HTTPException(status_code=400, detail=f"用户「{username}」已存在")

    try:
        user = db_create_user(
            username=username,
            role=role,
            display_name=display_name,
            note=note,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 写入密码（SHA-256 哈希）
    hashed = hash_password(password)
    set_override(username, hashed)

    # 审计日志
    add_operation_log(
        username=admin,
        action="create_user",
        target=username,
        detail=f"角色={role}",
        ip_address=_get_client_ip(request),
    )

    return user


@router.put("/users/{username}")
def update_user(
    username: str,
    req: dict,
    request: Request,
    admin: str = Depends(_require_admin),
):
    """编辑用户

    Body: { role?, display_name?, note?, status? }
    不允许修改 admin 用户的关键字段
    """
    if username == settings.ADMIN_USERNAME and req.get("role"):
        # admin 用户不能降级
        if req["role"] != "admin":
            raise HTTPException(status_code=400, detail="不能修改 admin 用户的角色")
    if username == settings.ADMIN_USERNAME and req.get("status"):
        if req["status"] == "disabled":
            raise HTTPException(status_code=400, detail="不能禁用 admin 用户")

    updated = db_update_user(
        username=username,
        role=req.get("role"),
        display_name=req.get("display_name"),
        status=req.get("status"),
        note=req.get("note"),
    )
    if not updated:
        raise HTTPException(status_code=404, detail="用户不存在或无变更")

    add_operation_log(
        username=admin,
        action="update_user",
        target=username,
        detail=json.dumps({k: v for k, v in req.items() if v is not None}, ensure_ascii=False),
        ip_address=_get_client_ip(request),
    )

    return {"success": True, "username": username}


@router.delete("/users/{username}")
def delete_user(
    username: str,
    request: Request,
    admin: str = Depends(_require_admin),
):
    """删除用户"""
    if username == settings.ADMIN_USERNAME:
        raise HTTPException(status_code=400, detail="不能删除 admin 用户")

    deleted = db_delete_user(username)
    if not deleted:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 同时清理密码覆盖
    delete_override(username)

    add_operation_log(
        username=admin,
        action="delete_user",
        target=username,
        ip_address=_get_client_ip(request),
    )

    return {"success": True, "username": username}


@router.post("/users/{username}/toggle-status")
def toggle_user_status(
    username: str,
    request: Request,
    admin: str = Depends(_require_admin),
):
    """启用/禁用用户"""
    if username == settings.ADMIN_USERNAME:
        raise HTTPException(status_code=400, detail="不能禁用 admin 用户")

    try:
        new_status = db_toggle_status(username)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    add_operation_log(
        username=admin,
        action="toggle_user_status",
        target=username,
        detail=f"新状态={new_status}",
        ip_address=_get_client_ip(request),
    )

    return {"success": True, "username": username, "status": new_status}


@router.post("/users/{username}/reset-password")
def reset_user_password(
    username: str,
    request: Request,
    admin: str = Depends(_require_admin),
):
    """重置用户密码（恢复为 .env 默认）"""
    deleted = delete_override(username)
    if not deleted:
        # 没有 override 记录也 OK（本来就是默认密码）
        pass

    add_operation_log(
        username=admin,
        action="reset_password",
        target=username,
        ip_address=_get_client_ip(request),
    )

    return {"success": True, "username": username, "message": "密码已恢复为默认值"}


@router.get("/users/{username}/history")
def get_user_history(
    username: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    admin: str = Depends(_require_admin),
):
    """查看某用户的查询历史"""
    return db_get_history(
        username=username,
        page=page,
        limit=limit,
    )


@router.get("/users/{username}/login-logs")
def get_user_login_logs(
    username: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    admin: str = Depends(_require_admin),
):
    """查看某用户的登录日志"""
    return db_get_login_logs(page=page, limit=limit, username_filter=username)


# ============================================================
# Incidents — 反馈事件
# ============================================================

@router.get("/incidents")
def list_incidents(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    type_filter: str = Query("", alias="type", description="筛选类型"),
    status_filter: str = Query("", alias="status", description="筛选状态"),
    env_filter: str = Query("", alias="env", description="筛选环境"),
    keyword: str = Query("", description="搜索问题关键词"),
    date_from: str = Query("", description="起始日期 YYYY-MM-DD"),
    date_to: str = Query("", description="结束日期 YYYY-MM-DD"),
    admin: str = Depends(_require_admin),
):
    """反馈事件列表"""
    return db_get_incidents(
        page=page,
        limit=limit,
        type_filter=type_filter,
        status_filter=status_filter,
        env_filter=env_filter,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/incidents/stats")
def incident_stats(admin: str = Depends(_require_admin)):
    """事件统计"""
    return db_get_incident_stats()


@router.get("/incidents/export")
def export_incidents(
    type_filter: str = Query("", alias="type"),
    status_filter: str = Query("", alias="status"),
    env_filter: str = Query("", alias="env"),
    admin: str = Depends(_require_admin),
):
    """导出事件报告 CSV"""
    data = db_get_incidents(
        page=1, limit=5000,
        type_filter=type_filter,
        status_filter=status_filter,
        env_filter=env_filter,
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["事件ID", "类型", "环境", "状态", "问题", "根因", "修复建议", "创建时间", "处理时间", "处理人"])
    for item in data["items"]:
        writer.writerow([
            item.get("id", ""),
            item.get("type", ""),
            item.get("env", ""),
            item.get("status", ""),
            item.get("question", ""),
            item.get("root_cause", ""),
            item.get("fix_proposal", ""),
            item.get("created_at", ""),
            item.get("resolved_at", ""),
            item.get("resolver", ""),
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=incidents_export.csv",
        },
    )


@router.get("/incidents/{incident_id}")
def get_incident_detail(
    incident_id: str,
    admin: str = Depends(_require_admin),
):
    """事件详情"""
    incident = db_get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="事件不存在")
    return incident


@router.put("/incidents/{incident_id}/status")
def update_incident_status(
    incident_id: str,
    req: dict,
    request: Request,
    admin: str = Depends(_require_admin),
):
    """变更事件状态

    Body: { status: "resolved" | "wontfix" | "pending" }
    """
    new_status = req.get("status", "")
    valid_statuses = {"pending", "resolved", "wontfix"}
    if new_status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"状态值无效，可选: {', '.join(valid_statuses)}",
        )

    updated = db_update_status(incident_id, new_status, resolver=admin)
    if not updated:
        raise HTTPException(status_code=404, detail="事件不存在")

    add_operation_log(
        username=admin,
        action="update_incident_status",
        target=incident_id,
        detail=f"新状态={new_status}",
        ip_address=_get_client_ip(request),
    )

    return {"success": True, "incident_id": incident_id, "status": new_status}


@router.post("/incidents/{incident_id}/analyze")
def analyze_incident_endpoint(
    incident_id: str,
    request: Request,
    admin: str = Depends(_require_admin),
):
    """触发 AI 重新分析事件根因（提交到扫描队列，由 cron AI 深度分析）"""
    incident = db_get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="事件不存在")

    # 写入 pending 事件文件，让下次 cron 扫描时由 AI 深度分析
    inc_file = {
        "incident_id": incident["id"],
        "type": incident["type"],
        "env": incident.get("env", "test"),
        "question": incident["question"],
        "sql": incident.get("sql", ""),
        "error": incident.get("error", ""),
        "warnings": incident.get("warnings", []),
        "intent_info": incident.get("intent_info", {}),
        "history_id": incident.get("history_id", ""),
        "feedback_comment": incident.get("feedback_comment", ""),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "pending",
    }

    # 写入测试环境的 feedback_review 目录
    feedback_dir = Path(os.path.expanduser("~/.lightclaw/workspace/star-query-test/feedback_review"))
    feedback_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = feedback_dir / f"inc_{ts}_test_{incident['id'].split('_')[-1]}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(inc_file, f, ensure_ascii=False, indent=2)

    # 清除旧的脚本级分析结果，标记为待 AI 分析
    db_update_analysis(
        incident_id,
        root_cause="等待 AI 深度分析",
        fix_proposal="已提交分析队列，将在下次定时扫描（每日10:00）中由 AI 进行深度分析",
    )

    add_operation_log(
        username=admin,
        action="analyze_incident",
        target=incident_id,
        detail="已提交 AI 分析队列",
        ip_address=_get_client_ip(request),
    )

    return {
        "success": True,
        "incident_id": incident_id,
        "message": "已提交分析队列，将在下次定时扫描中由 AI 进行深度分析",
        "root_cause": "等待 AI 深度分析",
        "fix_proposal": "已提交分析队列，将在下次定时扫描（每日10:00）中由 AI 进行深度分析",
    }


@router.post("/incidents/{incident_id}/apply-fix")
def apply_fix_endpoint(
    incident_id: str,
    request: Request,
    admin: str = Depends(_require_admin),
):
    """提交修复申请到 AI 队列（由 AI 在 cron 扫描时执行真正代码变更）"""
    incident = db_get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="事件不存在")

    fix_proposal = incident.get("fix_proposal", "")
    if not fix_proposal:
        raise HTTPException(status_code=400, detail="该事件暂无修复方案，请先执行 AI 分析")

    # 1. 写入 feedback_review/ pending_fix 标记文件
    feedback_dir = Path(os.path.expanduser("~/.lightclaw/workspace/star-query-test/feedback_review"))
    feedback_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    fix_file = {
        "incident_id": incident_id,
        "type": "fix_request",
        "status": "pending_fix",
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "question": incident.get("question", ""),
        "root_cause": incident.get("root_cause", ""),
        "fix_proposal": fix_proposal,
    }
    fix_path = feedback_dir / f"fix_{incident_id}.json"
    with open(fix_path, "w", encoding="utf-8") as f:
        json.dump(fix_file, f, ensure_ascii=False, indent=2)

    # 2. 更新 SQLite 状态
    db_update_fix_status(incident_id, "queue_fix", "已提交 AI 执行队列，将在下次扫描中执行")

    add_operation_log(
        username=admin,
        action="apply_fix",
        target=incident_id,
        detail=f"已提交 AI 执行队列",
        ip_address=_get_client_ip(request),
    )

    return {
        "success": True,
        "incident_id": incident_id,
        "fix_status": "queue_fix",
        "message": "修复申请已提交 ✅\nAI 将在下次定时扫描（每日 10:00）时执行具体代码变更、重启测试环境并运行回归验证。\n届时你会在 dashboard 看到详细的执行报告。",
    }


def _parse_fix_proposal(proposal: str) -> list[dict]:
    """从 fix_proposal 文本中解析文件修改建议"""
    changes = []
    # 尝试匹配 "file: xxx" 或 "文件: xxx" 模式
    for line in proposal.split("\n"):
        line = line.strip()
        for prefix in ["file:", "文件:", "- file:", "- 文件:"]:
            if line.lower().startswith(prefix.lower()):
                parts = line[len(prefix):].strip().split("—", 1)
                fpath = parts[0].strip()
                fchange = parts[1].strip() if len(parts) > 1 else ""
                if fpath:
                    changes.append({"file": fpath, "change": fchange})
                break
    return changes


def _safe_restart(test_dir: Path) -> str:
    """安全重启测试环境"""
    restart_script = test_dir / "safe-restart.sh"
    if restart_script.exists():
        result = subprocess.run(
            ["bash", str(restart_script), "--test"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            return f"重启完成: {result.stdout[:200]}"
        else:
            return f"重启警告: {result.stderr[:200]}"
    return "无 safe-restart.sh，跳过重启"


def _run_regression_test(test_dir: Path) -> tuple[bool, str]:
    """运行回归测试"""
    regress_script = test_dir / "backend" / "regression_test.py"
    if not regress_script.exists():
        return True, "无 regression_test.py，跳过回归测试"

    result = subprocess.run(
        [sys.executable or "python3", str(regress_script), "--regtest"],
        capture_output=True, text=True, timeout=120,
        cwd=str(test_dir),
    )
    if result.returncode == 0:
        return True, f"回归测试通过\n{result.stdout[:300]}"
    else:
        return False, f"回归测试失败\n{result.stdout[:300]}\n{result.stderr[:300]}"


@router.post("/incidents/batch-status")
def batch_update_status(
    req: dict,
    request: Request,
    admin: str = Depends(_require_admin),
):
    """批量变更事件状态

    Body: { ids: [...], status: "resolved" | "wontfix" }
    """
    ids = req.get("ids", [])
    new_status = req.get("status", "")
    valid_statuses = {"resolved", "wontfix"}
    if new_status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"批量操作仅支持: {', '.join(valid_statuses)}",
        )
    if not ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")

    count = db_batch_status(ids, new_status, resolver=admin)

    add_operation_log(
        username=admin,
        action="batch_update_incident_status",
        target=f"{len(ids)}条",
        detail=f"新状态={new_status}, 成功={count}",
        ip_address=_get_client_ip(request),
    )

    return {"success": True, "updated": count, "status": new_status}


@router.post("/incidents/batch-delete")
def batch_delete_incidents(
    req: dict,
    request: Request,
    admin: str = Depends(_require_admin),
):
    """批量删除事件

    Body: { ids: [...] }
    """
    ids = req.get("ids", [])
    if not ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")

    count = db_batch_delete(ids)

    add_operation_log(
        username=admin,
        action="batch_delete_incidents",
        target=f"{len(ids)}条",
        detail=f"成功删除={count}",
        ip_address=_get_client_ip(request),
    )

    return {"success": True, "deleted": count}


@router.delete("/incidents/{incident_id}")
def delete_incident_endpoint(
    incident_id: str,
    request: Request,
    admin: str = Depends(_require_admin),
):
    """删除单条事件"""
    deleted = db_delete_incident(incident_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="事件不存在")

    add_operation_log(
        username=admin,
        action="delete_incident",
        target=incident_id,
        ip_address=_get_client_ip(request),
    )

    return {"success": True, "incident_id": incident_id}


# ============================================================
# Login Logs — 登录日志（全量）
# ============================================================

@router.get("/login-logs")
def list_login_logs(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    username: str = Query(""),
    date_from: str = Query(""),
    date_to: str = Query(""),
    admin: str = Depends(_require_admin),
):
    """全量登录日志"""
    return db_get_login_logs(page=page, limit=limit, username_filter=username)


# ============================================================
# Sync — 同步测试环境修改到正式环境
# ============================================================

@router.post("/sync-to-prod")
def sync_to_prod(
    request: Request,
    admin: str = Depends(_require_admin),
):
    """将测试环境的改动同步到正式环境

    流程：
    1. 对比测试/正式环境的文件差异
    2. 复制变更文件
    3. 重建前端（如有前端变更）
    4. 重启正式环境
    """
    workspace = Path(os.path.expanduser("~/.lightclaw/workspace"))
    test_dir = workspace / "star-query-test"
    prod_dir = workspace / "star-query"

    changes = []

    # ============================================================
    # 全量动态扫描：检测 test/prod 所有后端 .py 和前端 src 文件
    # ============================================================

    def _scan_file_md5(rel_path: str, file_type: str) -> tuple[bool, str, str]:
        """对比单个文件的 md5，返回 (有差异, 差异描述, 相对路径)"""
        test_file = test_dir / rel_path
        prod_file = prod_dir / rel_path

        if not test_file.exists():
            return (False, "", "")

        if not prod_file.exists():
            # 新增文件（test 有，prod 无）
            try:
                test_lines = len(test_file.read_text().splitlines())
                diff_str = f"新增文件（{test_lines}行）"
            except Exception:
                diff_str = "新增文件"
            return (True, diff_str, rel_path)

        # 两者都存在 → 比较 md5
        test_md5 = subprocess.run(
            ["md5sum", str(test_file)], capture_output=True, text=True
        ).stdout.split()[0]
        prod_md5 = subprocess.run(
            ["md5sum", str(prod_file)], capture_output=True, text=True
        ).stdout.split()[0]

        if test_md5 == prod_md5:
            return (False, "", "")

        # 有差异 → 统计行数变化
        try:
            test_lines = len(test_file.read_text().splitlines())
            prod_lines = len(prod_file.read_text().splitlines())
            diff = test_lines - prod_lines
            diff_str = f"+{diff}行" if diff > 0 else f"{diff}行" if diff < 0 else "内容有修改"
        except Exception:
            diff_str = "内容有修改"
        return (True, diff_str, rel_path)

    # 后端：动态扫描 all .py（排除 __pycache__）
    backend_py_files = sorted(
        p.relative_to(test_dir).as_posix()
        for p in test_dir.glob("backend/*.py")
        if "__pycache__" not in p.name
    )
    for rel_path in backend_py_files:
        changed, diff_str, _ = _scan_file_md5(rel_path, "backend")
        if changed:
            changes.append({"file": rel_path, "diff": diff_str, "type": "backend"})

    # 前端：动态扫描 frontend/src/ 下常见源码文件（.tsx, .ts, .css, .jsx）
    frontend_changed = False
    frontend_extensions = (".tsx", ".ts", ".css", ".jsx", ".js", ".json", ".html")
    frontend_src_files = sorted(
        p.relative_to(test_dir).as_posix()
        for p in test_dir.rglob("frontend/src/*")
        if p.suffix in frontend_extensions and "__pycache__" not in p.name
    )
    for rel_path in frontend_src_files:
        changed, diff_str, _ = _scan_file_md5(rel_path, "frontend")
        if changed:
            frontend_changed = True
            changes.append({"file": rel_path, "diff": diff_str, "type": "frontend"})

    if not changes:
        return {"success": True, "message": "✅ 测试环境和正式环境已一致，无需同步", "changes": []}

    # 执行同步
    sync_log = []
    errors = []

    # 复制后端文件
    for c in changes:
        if c["type"] != "frontend":
            rel = c["file"]
            src = test_dir / rel
            dst = prod_dir / rel
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(src.read_bytes())
                sync_log.append(f"  ✅ {rel} 已同步")
            except Exception as e:
                errors.append(f"  ❌ {rel} 同步失败: {e}")

    # 复制前端源文件 + 编译
    if frontend_changed:
        for c in changes:
            if c["type"] == "frontend":
                rel = c["file"]
                src = test_dir / rel
                dst = prod_dir / rel
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(src.read_bytes())
                    sync_log.append(f"  ✅ {rel} 已同步")
                except Exception as e:
                    errors.append(f"  ❌ {rel} 同步失败: {e}")

        # rebuild
        try:
            result = subprocess.run(
                ["npx", "vite", "build"],
                capture_output=True, text=True, timeout=120,
                cwd=str(prod_dir / "frontend"),
            )
            if result.returncode == 0:
                sync_log.append(f"  ✅ 前端编译成功")
            else:
                errors.append(f"  ❌ 前端编译失败: {result.stderr[-200:]}")
        except subprocess.TimeoutExpired:
            errors.append("  ❌ 前端编译超时")

    # 重启正式环境
    restart_log = ""
    restart_script = prod_dir / "safe-restart.sh"
    if restart_script.exists() and not errors:
        try:
            result = subprocess.run(
                ["bash", str(restart_script)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                restart_log = "✅ 正式环境重启成功"
                sync_log.append(f"  ✅ 正式环境已重启")
            else:
                restart_log = f"⚠️ 重启结果: {result.stderr[-200:] or result.stdout[-200:]}"
                sync_log.append(f"  {restart_log}")
        except subprocess.TimeoutExpired:
            errors.append("  ❌ 重启超时")

    # 审计日志
    summary = "; ".join([c["file"] for c in changes])
    add_operation_log(
        username=admin,
        action="sync_to_prod",
        target="",
        detail=f"同步文件: {summary}",
        ip_address=_get_client_ip(request),
    )

    return {
        "success": len(errors) == 0,
        "message": "✅ 同步完成" if not errors else f"⚠️ 同步完成但有错误",
        "changes": changes,
        "sync_log": sync_log,
        "errors": errors,
        "restart": restart_log,
    }

@router.get("/operation-logs")
def list_operation_logs(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    username: str = Query(""),
    action: str = Query(""),
    admin: str = Depends(_require_admin),
):
    """操作审计日志"""
    from admin_store import get_operation_logs
    return get_operation_logs(
        page=page, limit=limit,
        username_filter=username,
        action_filter=action,
    )


# ============================================================
# Query History — 全平台查询记录
# ============================================================

@router.get("/query-history")
def list_query_history(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    username: str = Query("", description="筛选用户"),
    keyword: str = Query("", description="搜索问题/SQL"),
    date_from: str = Query("", description="起始日期 YYYY-MM-DD"),
    date_to: str = Query("", description="结束日期 YYYY-MM-DD"),
    admin: str = Depends(_require_admin),
):
    """全平台查询记录"""
    return db_get_all_history(
        page=page,
        limit=limit,
        username=username,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
    )


# ============================================================
# Data Health — 数据接口健康状态
# ============================================================

@router.get("/data-health")
def data_health(admin: str = Depends(_require_admin)):
    """数据接口健康状态

    返回：
    - 数据文件信息（更新时间、大小）
    - 当前数据日期范围
    - 近30天每日场景数
    - 同步日志状态
    - 健康告警
    """
    from sql_engine import engine
    data_path = Path(settings.DATA_PATH)

    result = {
        "data_file": {},
        "data_coverage": {},
        "daily_trend": [],
        "sync_status": {},
        "alerts": [],
    }

    # ---- 1. 文件信息 ----
    if data_path.exists():
        stat = data_path.stat()
        result["data_file"] = {
            "path": str(data_path),
            "size_mb": round(stat.st_size / 1024 / 1024, 1),
            "last_modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "filename": data_path.name,
        }

    # ---- 2. 数据日期覆盖 ----
    try:
        dq = engine.execute("""
            SELECT
                MIN(ydate) AS min_date,
                MAX(ydate) AS max_date,
                COUNT(DISTINCT ydate) AS total_days,
                COUNT(DISTINCT 场景ID) AS total_scenes
            FROM data
        """)
        if dq["success"] and dq["rows"]:
            r = dq["rows"][0]
            result["data_coverage"] = {
                "min_date": str(r["min_date"])[:10] if r["min_date"] else None,
                "max_date": str(r["max_date"])[:10] if r["max_date"] else None,
                "total_days": r["total_days"],
                "total_scenes": r["total_scenes"],
            }
    except Exception as e:
        result["alerts"].append(f"❌ 数据查询失败: {e}")

    # ---- 3. 近30天每日场景数（排除1970脏数据） ----
    try:
        trend = engine.execute("""
            SELECT ydate, COUNT(DISTINCT 场景ID) AS scenes
            FROM data
            WHERE ydate >= '2026-01-01'
            GROUP BY ydate
            ORDER BY ydate DESC
            LIMIT 35
        """)
        if trend["success"]:
            result["daily_trend"] = [
                {
                    "date": str(r["ydate"])[:10],
                    "scenes": r["scenes"],
                }
                for r in trend["rows"]
            ]
    except Exception as e:
        result["alerts"].append(f"❌ 每日趋势查询失败: {e}")

    # ---- 4. 同步日志状态 ----
    log_file = Path("/var/log/clickhouse-daily-sync.log")
    if log_file.exists():
        try:
            log_text = log_file.read_text(encoding="utf-8", errors="ignore")
            lines = log_text.strip().split("\n")
            # 解析每次同步的摘要：找"开始增量同步"标记
            sync_runs = []
            for i, line in enumerate(lines):
                if "开始增量同步" in line:
                    # 提取时间
                    ts = line[1:20] if line.startswith("[") else ""
                    # 看后面有没有 "拉取行数" 和 "成功/失败"
                    rows_pulled = 0
                    status = "unknown"
                    for j in range(i, min(i + 30, len(lines))):
                        l = lines[j]
                        if "拉取行数:" in l:
                            try:
                                rows_pulled = int(l.split("拉取行数:")[-1].strip())
                            except:
                                pass
                        if "✅" in l and ("合并" in l or "完成" in l):
                            status = "success"
                        if "无数据" in l:
                            status = "no_data"
                        if "error" in l.lower() or "失败" in l or "Error" in l:
                            status = "error"
                    sync_runs.append({
                        "time": ts,
                        "rows_pulled": rows_pulled,
                        "status": status,
                    })
            result["sync_status"] = {
                "log_file": str(log_file),
                "last_5_runs": sync_runs[-5:] if sync_runs else [],
                "total_runs": len(sync_runs),
            }
        except Exception as e:
            result["sync_status"] = {"error": str(e)}
    else:
        result["sync_status"] = {"note": "同步日志文件不存在"}

    # ---- 5. 健康告警 ----
    alerts = []

    # 5a. 数据最后更新距今几天
    last_modified = result["data_file"].get("last_modified", "")
    max_date = result["data_coverage"].get("max_date", "")
    if last_modified:
        from datetime import datetime as dt
        lm = dt.strptime(last_modified[:10], "%Y-%m-%d")
        days_since_update = (dt.now() - lm).days
        if days_since_update > 1:
            alerts.append({
                "level": "warning",
                "message": f"️️⚠️ 数据文件已 {days_since_update} 天未更新（最后: {last_modified[:10]}）",
            })

    # 5b. 最新数据日期距今几天
    if max_date:
        from datetime import datetime as dt
        md = dt.strptime(max_date, "%Y-%m-%d")
        days_since_data = (dt.now() - md).days
        if days_since_data > 2:
            alerts.append({
                "level": "warning",
                "message": f"⚠️ 最新数据停留在 {max_date}（距今 {days_since_data} 天）",
            })

    # 5c. 同步日志最近状态
    last_runs = result["sync_status"].get("last_5_runs", [])
    if last_runs:
        latest = last_runs[-1]
        if latest["status"] == "no_data":
            alerts.append({
                "level": "info",
                "message": f"📡 最近一次同步（{latest['time']}）无数据，可能是当天无交易或源数据未更新",
            })
        elif latest["status"] == "error":
            alerts.append({
                "level": "error",
                "message": f"❌ 最近一次同步（{latest['time']}）执行出错，请检查日志",
            })

    # 5d. 连续多天无数据
    no_data_days = sum(1 for r in last_runs if r["status"] == "no_data" and r["rows_pulled"] == 0)
    if no_data_days >= 3:
        alerts.append({
            "level": "warning",
            "message": f"⚠️ 连续 {no_data_days} 天同步到 0 条数据，请确认以下可能性：①当天确实无交易 ②ClickHouse 源数据未更新 ③同事尚未完成数据清洗",
        })

    result["alerts"] = alerts

    return result



# ============================================================
# Download Records — 全平台下载记录
# ============================================================

from download_store import get_all_records as get_all_downloads


@router.get("/download-records")
def list_download_records(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    admin: str = Depends(_require_admin),
):
    """全平台数据导出下载记录"""
    return get_all_downloads(page=page, limit=limit)
