"""星宝语料场景查询系统 — JWT 认证模块"""

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from config import settings

# --- 密码加密（PBKDF2-HMAC-SHA256，加盐） ---

HASH_ITERATIONS = 100_000


def hash_password(plain: str, salt: str = None) -> str:
    """PBKDF2-HMAC-SHA256 密码哈希（加盐）"""
    import os
    if salt is None:
        salt = os.urandom(16).hex()
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        plain.encode("utf-8"),
        salt.encode("utf-8"),
        HASH_ITERATIONS,
    )
    return f"pbkdf2${salt}${derived.hex()}"


def verify_password(plain: str, stored: str) -> bool:
    """验证密码（仅支持 pbkdf2$ 格式，不再兼容明文）"""
    if not stored.startswith("pbkdf2$"):
        # 旧数据迁移：如果存储的是 sha256$，升级到 pbkdf2$
        if stored.startswith("sha256$"):
            return False  # 拒绝旧格式登录，要求重置密码
        return False
    parts = stored.split("$")
    if len(parts) != 4:
        return False
    _, _, salt, digest = parts
    expected = hash_password(plain, salt)
    return expected == stored


# --- JWT ---
security = HTTPBearer(auto_error=False)


def create_access_token(username: str, expires_hours: Optional[int] = None) -> str:
    """签发 JWT Token"""
    if expires_hours is None:
        expires_hours = settings.JWT_EXPIRES_HOURS
    expire = datetime.now(timezone.utc) + timedelta(hours=expires_hours)
    payload = {
        "sub": username,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """解码并验证 JWT Token"""
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM]
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 已过期，请重新登录",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的 Token",
        )


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> str:
    """依赖注入：获取当前登录用户名"""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(credentials.credentials)
    return payload.get("sub", "unknown")