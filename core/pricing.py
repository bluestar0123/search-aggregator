"""计费模块 - 用量记录、余额管理、配额检查、用量统计"""
from __future__ import annotations

from typing import Any

from core.database import get_db


# ============================================================
# 用量记录
# ============================================================

async def record_usage(
    api_key_id: int,
    user_id: int,
    query: str,
    channels: str,
    results_count: int,
    latency_ms: float,
    cost: float,
    status: str = "ok",
    error_msg: str | None = None,
    ip_address: str | None = None,
) -> None:
    """记录一次 API 调用的用量日志"""
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO usage_logs
               (api_key_id, user_id, query, channels, results_count,
                latency_ms, cost, status, error_msg, ip_address)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (api_key_id, user_id, query, channels, results_count,
             latency_ms, cost, status, error_msg, ip_address),
        )
        await db.commit()
    finally:
        await db.close()


# ============================================================
# 余额管理
# ============================================================

async def check_balance(user_id: int) -> float:
    """查询用户账户余额，用户不存在返回 0.0"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT balance FROM api_users WHERE id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return float(row["balance"]) if row else 0.0
    finally:
        await db.close()


async def deduct_balance(user_id: int, amount: float) -> bool:
    """扣费，余额不足返回 False（不执行扣减）

    使用 SQL 原子操作避免并发问题。
    """
    db = await get_db()
    try:
        # 先检查余额
        cursor = await db.execute(
            "SELECT balance FROM api_users WHERE id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return False

        current_balance = float(row["balance"])
        if current_balance < amount:
            return False

        await db.execute(
            "UPDATE api_users SET balance = balance - ? WHERE id = ?",
            (amount, user_id),
        )
        await db.commit()
        return True
    finally:
        await db.close()


# ============================================================
# 配额检查
# ============================================================

async def check_quota(api_key_id: int) -> bool:
    """检查 API Key 的日调用配额是否已超限。

    返回 True 表示配额内可以调用，False 表示已超限。
    """
    db = await get_db()
    try:
        # 获取该 key 的日配额
        cursor = await db.execute(
            "SELECT quota_per_day FROM api_keys WHERE id = ? AND enabled = 1",
            (api_key_id,),
        )
        key_row = await cursor.fetchone()
        if key_row is None:
            return False  # key 不存在或已禁用

        quota_per_day = key_row["quota_per_day"]

        # 统计今日调用次数 (以 UTC 日期为准)
        cursor = await db.execute(
            """SELECT COUNT(*) as cnt FROM usage_logs
               WHERE api_key_id = ?
                 AND DATE(created_at) = DATE('now')""",
            (api_key_id,),
        )
        count_row = await cursor.fetchone()
        today_count = count_row["cnt"] if count_row else 0

        return today_count < quota_per_day
    finally:
        await db.close()


# ============================================================
# 用量统计
# ============================================================

async def get_user_usage(user_id: int, days: int = 30) -> dict[str, Any]:
    """获取用户在最近 N 天的用量统计。

    返回:
        {
            "user_id": int,
            "total_calls": int,
            "total_cost": float,
            "daily": [
                {"date": "2025-01-15", "calls": 42, "cost": 0.42, "errors": 2},
                ...
            ]
        }
    """
    db = await get_db()
    try:
        # 总调用和总花费
        cursor = await db.execute(
            """SELECT COUNT(*) as total_calls,
                      COALESCE(SUM(cost), 0) as total_cost
               FROM usage_logs
               WHERE user_id = ?
                 AND created_at >= DATE('now', ?)""",
            (user_id, f"-{days} days"),
        )
        total_row = await cursor.fetchone()
        total_calls = total_row["total_calls"] if total_row else 0
        total_cost = float(total_row["total_cost"]) if total_row else 0.0

        # 每日明细
        cursor = await db.execute(
            """SELECT DATE(created_at) as date,
                      COUNT(*) as calls,
                      COALESCE(SUM(cost), 0) as cost,
                      SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END) as errors
               FROM usage_logs
               WHERE user_id = ?
                 AND created_at >= DATE('now', ?)
               GROUP BY DATE(created_at)
               ORDER BY date DESC""",
            (user_id, f"-{days} days"),
        )
        rows = await cursor.fetchall()
        daily = [
            {
                "date": row["date"],
                "calls": row["calls"],
                "cost": float(row["cost"]),
                "errors": row["errors"],
            }
            for row in rows
        ]

        return {
            "user_id": user_id,
            "total_calls": total_calls,
            "total_cost": total_cost,
            "daily": daily,
        }
    finally:
        await db.close()


async def get_all_usage_summary() -> dict[str, Any]:
    """所有用户的用量汇总，用于管理员 Dashboard。

    返回:
        {
            "total_users": int,
            "total_calls": int,
            "total_cost": float,
            "users": [
                {
                    "user_id": int,
                    "username": str,
                    "balance": float,
                    "total_calls": int,
                    "total_cost": float,
                },
                ...
            ]
        }
    """
    db = await get_db()
    try:
        # 所有用户汇总
        cursor = await db.execute(
            """SELECT
                 au.id as user_id,
                 au.username,
                 au.balance,
                 COALESCE(SUM(ul.cost), 0) as total_cost,
                 COUNT(ul.id) as total_calls
               FROM api_users au
               LEFT JOIN usage_logs ul ON au.id = ul.user_id
               GROUP BY au.id
               ORDER BY total_cost DESC"""
        )
        rows = await cursor.fetchall()
        users = [
            {
                "user_id": row["user_id"],
                "username": row["username"],
                "balance": float(row["balance"]),
                "total_calls": row["total_calls"],
                "total_cost": float(row["total_cost"]),
            }
            for row in rows
        ]

        total_calls = sum(u["total_calls"] for u in users)
        total_cost = sum(u["total_cost"] for u in users)

        return {
            "total_users": len(users),
            "total_calls": total_calls,
            "total_cost": total_cost,
            "users": users,
        }
    finally:
        await db.close()
