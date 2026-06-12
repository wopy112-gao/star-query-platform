"""星宝语料场景查询系统 — JWT 认证模块"""

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from config import settings

# --- 密码加密（SHA-256，绕开 bcrypt 兼容性问题） ---

PWD_PREFIX = "sha256$"


def hash_password(plain: str) -> str:
    """SHA-256 密码哈希"""
    return PWD_PREFIX + hashlib.sha256(plain.encode()).hexdigest()


def verify_password(plain: str, stored: str) -> bool:
    """验证密码（兼容 plaintext + sha256$ 两种格式）"""
    if stored.startswith(PWD_PREFIX):
        return stored == hash_password(plain)
    # 明文比较（.env 中的原始密码）
    return plain == stored


# --- JWT ---
security = HTTPBearer(auto_error=False)


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
