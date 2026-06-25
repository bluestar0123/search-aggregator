"""认证模块 - JWT 令牌、密码哈希、用户/API Key 认证、FastAPI 中间件"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException, Request

from core.database import get_db

# ============================================================
# 配置 (从环境变量读取，敏感信息不硬编码)
# ============================================================

JWT_SECRET = os.getenv("JWT_SECRET", "")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET 环境变量未设置，请在 .env 中配置")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "24"))


# ============================================================
# 密码相关
# ============================================================

def hash_password(password: str) -> str:
    """使用 bcrypt 哈希密码"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """验证密码与哈希是否匹配"""
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"), hashed.encode("utf-8")
        )
    except Exception:
        return False


# ============================================================
# JWT 令牌
# ============================================================

def create_jwt(data: dict[str, Any]) -> str:
    """创建 JWT token，自动添加 exp 过期时间"""
    payload = dict(data)
    payload["exp"] = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    payload["iat"] = datetime.now(timezone.utc)
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_jwt(token: str) -> dict[str, Any] | None:
    """验证 JWT token，成功返回 payload，失败/过期返回 None"""
    try:
        payload: dict[str, Any] = jwt.decode(
            token, JWT_SECRET, algorithms=[JWT_ALGORITHM]
        )
        return payload
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


# ============================================================
# 用户认证 (统一使用 api_users 表)
# ============================================================

async def verify_user(username: str, password: str) -> dict[str, Any] | None:
    """验证用户登录（api_users 表），成功返回 {username, role}，失败返回 None"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT username, password_hash, role FROM api_users WHERE username = ?",
            (username,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        if not verify_password(password, row["password_hash"]):
            return None
        return {"username": row["username"], "role": row["role"]}
    finally:
        await db.close()


# ============================================================
# API Key 相关
# ============================================================

def generate_api_key() -> str:
    """生成 sk-{8位hex}-{24位hex} 格式的 API Key"""
    prefix_part = secrets.token_hex(4)   # 8 位 hex
    secret_part = secrets.token_hex(12)  # 24 位 hex
    return f"sk-{prefix_part}-{secret_part}"


async def create_api_key(
    user_id: int,
    quota_per_day: int = 100,
    quota_per_month: int = 3000,
    price_per_call: float = 0.01,
) -> dict[str, Any]:
    """为指定用户创建一个 API Key。

    返回:
        {
            "key": "sk-xxx...xxxx",  # 明文 key（只返回一次）
            "key_prefix": "sk-xxxx...",
            "id": int,
            "quota_per_day": int,
            "quota_per_month": int,
            "price_per_call": float,
        }
    """
    raw_key = generate_api_key()
    key_prefix = raw_key[:12]  # "sk-xxxxxxxx" 部分用于快速匹配
    key_hashed = hash_password(raw_key)

    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO api_keys
               (user_id, key_prefix, key_hash, enabled,
                quota_per_day, quota_per_month, price_per_call)
               VALUES (?, ?, ?, 1, ?, ?, ?)""",
            (user_id, key_prefix, key_hashed,
             quota_per_day, quota_per_month, price_per_call),
        )
        await db.commit()
        key_id = cursor.lastrowid
        return {
            "key": raw_key,
            "key_prefix": key_prefix,
            "id": key_id,
            "quota_per_day": quota_per_day,
            "quota_per_month": quota_per_month,
            "price_per_call": price_per_call,
        }
    finally:
        await db.close()


async def verify_api_key(api_key: str) -> dict[str, Any] | None:
    """验证 API Key，返回 key 信息或 None。

    返回 (成功时):
        {
            "key_id": int,
            "user_id": int,
            "username": str,
            "quota_per_day": int,
            "quota_per_month": int,
            "price_per_call": float,
        }
    """
    key_prefix = api_key[:12] if len(api_key) >= 12 else api_key

    db = await get_db()
    try:
        # 用前缀快速缩小范围
        cursor = await db.execute(
            """SELECT ak.id, ak.user_id, ak.key_hash,
                      ak.enabled, ak.quota_per_day, ak.quota_per_month,
                      ak.price_per_call,
                      au.username
               FROM api_keys ak
               JOIN api_users au ON ak.user_id = au.id
               WHERE ak.key_prefix = ? AND ak.enabled = 1""",
            (key_prefix,),
        )
        rows = await cursor.fetchall()
        for row in rows:
            if verify_password(api_key, row["key_hash"]):
                return {
                    "key_id": row["id"],
                    "user_id": row["user_id"],
                    "username": row["username"],
                    "quota_per_day": row["quota_per_day"],
                    "quota_per_month": row["quota_per_month"],
                    "price_per_call": row["price_per_call"],
                }
        return None
    finally:
        await db.close()


async def revoke_api_key(key_id: int) -> bool:
    """删除指定 API Key（级联删除关联的 usage_logs），返回是否成功"""
    db = await get_db()
    try:
        # 先删除关联的使用日志（外键约束）
        await db.execute("DELETE FROM usage_logs WHERE api_key_id = ?", (key_id,))
        cursor = await db.execute(
            "DELETE FROM api_keys WHERE id = ?", (key_id,)
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def get_user_keys(user_id: int) -> list[dict[str, Any]]:
    """获取用户的所有 API Key 列表（不含明文 key，仅显示前缀）"""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT id, key_prefix, enabled, quota_per_day,
                      quota_per_month, price_per_call, created_at
               FROM api_keys
               WHERE user_id = ?
               ORDER BY created_at DESC""",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row["id"],
                "key_prefix": row["key_prefix"] + "...",
                "enabled": bool(row["enabled"]),
                "quota_per_day": row["quota_per_day"],
                "quota_per_month": row["quota_per_month"],
                "price_per_call": row["price_per_call"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    finally:
        await db.close()


# ============================================================
# FastAPI 依赖注入 (Dependency Injection)
# ============================================================

def _extract_token(request: Request) -> str | None:
    """从 Cookie 或 Authorization Header 提取 JWT token"""
    # 1. 优先从 Cookie 读取
    cookie_token = request.cookies.get("token")
    if cookie_token:
        return cookie_token

    # 2. 其次从 Authorization Header 读取
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]

    return None


async def require_admin(request: Request) -> dict[str, Any]:
    """FastAPI Depends：验证管理员（admin 角色）权限。

    成功返回 {"username": str, "role": str}
    失败抛出 HTTPException(401/403)
    """
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="未登录：缺少认证令牌")

    payload = verify_jwt(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="认证令牌无效或已过期")

    role = payload.get("role", "")
    if role != "admin":
        raise HTTPException(status_code=403, detail="权限不足：需要管理员身份")

    return {"username": payload.get("username", ""), "role": role}


async def require_login(request: Request) -> dict[str, Any]:
    """FastAPI Depends：验证用户已登录（任意角色均可）。

    成功返回 {"username": str, "role": str}
    失败抛出 HTTPException(401)
    """
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="未登录：缺少认证令牌")

    payload = verify_jwt(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="认证令牌无效或已过期")

    return {"username": payload.get("username", ""), "role": payload.get("role", "")}


async def require_api_key(
    request: Request,
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> dict[str, Any]:
    """FastAPI Depends：从 X-API-Key Header 读取并验证 API Key。

    成功返回 {"key_id": int, "user_id": int, "username": str,
              "quota_per_day": int, "quota_per_month": int,
              "price_per_call": float}
    失败抛出 HTTPException(401)
    """
    if not x_api_key:
        raise HTTPException(status_code=401, detail="缺少 X-API-Key Header")

    info = await verify_api_key(x_api_key)
    if info is None:
        raise HTTPException(status_code=401, detail="API Key 无效或已禁用")

    return info
