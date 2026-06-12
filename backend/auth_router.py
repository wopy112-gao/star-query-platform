"""星宝语料场景查询系统 — 认证路由"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status

from auth import (
    verify_password,
    hash_password,
    create_access_token,
    decode_token,
    get_current_user,
)
from config import settings
from models import (
    LoginRequest,
    LoginResponse,
    VerifyResponse,
    LoginLogItem,
    LoginLogsResponse,
    ChangePasswordRequest,
    ChangePasswordResponse,
)
from login_store import add_login_log, get_login_logs
from password_store import get_override, set_override
from admin_store import is_user_disabled, update_last_login

router = APIRouter(prefix="/api/auth", tags=["认证"])


def _get_client_ip(request: Request) -> str:
    """获取客户端真实 IP（优先 X-Forwarded-For，其次 client.host）"""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = request.client
    if client:
        return client.host
    return ""


@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest, request: Request):
    """登录（支持管理员 + 多用户）"""
    ip_address = _get_client_ip(request)
    user_agent = request.headers.get("User-Agent", "")

    # 判断用户属于哪个用户池
    # 优先级: .env > users 表（管理后台新增用户）
    password = None
    is_db_user = False  # 是否来自 users 表

    if req.username == settings.ADMIN_USERNAME:
        password = settings.ADMIN_PASSWORD
    elif req.username in settings.USERS:
        password = settings.USERS[req.username]
    else:
        # 检查 users 表（管理后台新增的用户，密码存储在 password_override）
        from admin_store import get_user as db_get_user
        db_user = db_get_user(req.username)
        if db_user is not None:
            from password_store import get_override as db_get_override
            override_hash = db_get_override(req.username)
            if override_hash:
                password = override_hash
                is_db_user = True

    if password is None:
        # 记录登录失败：用户名不存在
        add_login_log(
            username=req.username,
            ip_address=ip_address,
            user_agent=user_agent,
            success=False,
            detail="用户名不存在",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    # 检查用户是否改过密码（password_override > .env 默认值）
    if is_db_user:
        stored_password = password
    else:
        override_hash = get_override(req.username)
        stored_password = override_hash if override_hash else password

    # 验证密码（兼容 plaintext + sha256$ 两种格式）
    password_ok = verify_password(req.password, stored_password)
    fail_reason = "" if password_ok else "密码错误"

    if not password_ok:
        add_login_log(
            username=req.username,
            ip_address=ip_address,
            user_agent=user_agent,
            success=False,
            detail=fail_reason,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    # 检查用户是否被禁用（管理后台操作）
    if is_user_disabled(req.username):
        add_login_log(
            username=req.username,
            ip_address=ip_address,
            user_agent=user_agent,
            success=False,
            detail="账号已被禁用",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已被禁用，请联系管理员",
        )

    # 记录最后登录时间
    update_last_login(req.username)

    # 签发 Token
    token = create_access_token(req.username)

    # 记录登录成功
    add_login_log(
        username=req.username,
        ip_address=ip_address,
        user_agent=user_agent,
        success=True,
        detail="登录成功",
    )

    return LoginResponse(
        token=token,
        expires_in=settings.JWT_EXPIRES_HOURS * 3600,
        username=req.username,
    )


@router.get("/login-logs", response_model=LoginLogsResponse)
def query_login_logs(
    page: int = 1,
    limit: int = 20,
    username: str = "",
    current_user: str = Depends(get_current_user),
):
    """获取登录日志（仅管理员可查看）"""
    if current_user != settings.ADMIN_USERNAME:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅管理员可查看登录日志",
        )
    return get_login_logs(page=page, limit=limit, username_filter=username)


@router.get("/verify", response_model=VerifyResponse)
def verify_token(username: str = Depends(get_current_user)):
    """验证 Token 有效性"""
    return VerifyResponse(valid=True, username=username)


@router.post("/change-password", response_model=ChangePasswordResponse)
def change_password(
    req: ChangePasswordRequest,
    request: Request,
    username: str = Depends(get_current_user),
):
    """修改当前登录用户的密码"""
    # 获取当前用户的原始密码（先查 override，再查 .env）
    raw_password = None

    if username == settings.ADMIN_USERNAME:
        raw_password = settings.ADMIN_PASSWORD
    elif username in settings.USERS:
        raw_password = settings.USERS[username]

    if raw_password is None:
        raise HTTPException(status_code=400, detail="用户不存在")

    # 检查 override 表是否有更优先的密码
    override_hash = get_override(username)
    stored_password = override_hash if override_hash else raw_password

    # 验证旧密码
    password_ok = verify_password(req.old_password, stored_password)

    if not password_ok:
        raise HTTPException(status_code=400, detail="旧密码错误")

    # 写入新密码（SHA-256 哈希）
    new_hash = hash_password(req.new_password)
    set_override(username, new_hash)

    return ChangePasswordResponse(
        success=True,
        message="密码修改成功，下次登录时生效",
    )
