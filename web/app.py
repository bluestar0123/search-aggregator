"""FastAPI Web 管理界面 - 搜索聚合器 (含认证鉴权)"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from core.config_manager import get_config_manager
from core.database import init_db, get_db
from core.key_manager import key_manager
from core.auth import (
    create_jwt,
    verify_jwt,
    verify_user,
    verify_api_key,
    hash_password,
    create_api_key,
    revoke_api_key,
    get_user_keys,
    require_admin,
    require_login,
    require_api_key,
)
from core.pricing import (
    record_usage,
    check_balance,
    check_quota,
    deduct_balance,
    get_user_usage,
    get_all_usage_summary,
)
from core.models import (
    ChannelUpdateRequest,
    KeyAddRequest,
    SearchRequest,
    SearchResponse,
)

# ============================================================
# FastAPI 应用
# ============================================================
app = FastAPI(
    title="Search Aggregator",
    description="多渠道搜索聚合管理界面",
    version="1.0.0",
)

# 模板目录
_templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))

# 空白 favicon.ico（避免浏览器重复请求 404）
_FAVICON = b""


@app.get("/favicon.ico")
async def favicon():
    """返回空 favicon，消除 404 噪音"""
    return Response(content=_FAVICON, media_type="image/x-icon")


@app.on_event("startup")
async def startup():
    """启动时初始化数据库"""
    await init_db()
    # 初始化 key_manager（聚合搜索依赖此判断渠道是否有可用 key）
    cm = get_config_manager()
    channel_keys = {name: cm.get_keys(name) for name in cm.get_all_channels()}
    key_manager.init_all(channel_keys)
    # 初始化配额计数器（从 usage_logs 加载今日/本月调用量）
    from core.rate_limiter import rate_limiter
    await rate_limiter.init_from_db()


def _cm():
    return get_config_manager()


# ============================================================
# 页面路由 (认证)
# ============================================================

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """登录页面"""
    return templates.TemplateResponse(request=request, name="login.html")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Web 管理界面首页 - 需要登录"""
    token = request.cookies.get("token") or request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        return RedirectResponse(url="/login", status_code=302)
    payload = verify_jwt(token)
    if not payload:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request=request, name="index.html")


# ============================================================
# 认证 API
# ============================================================

@app.post("/api/auth/login")
async def api_login(request: Request):
    """用户登录（统一 api_users 表），成功后设置Cookie"""
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")

    if not username or not password:
        raise HTTPException(400, "用户名和密码不能为空")

    user = await verify_user(username, password)
    if not user:
        raise HTTPException(400, "用户名或密码错误")

    token = create_jwt({"username": user["username"], "role": user["role"]})
    response = JSONResponse({"token": token, "username": user["username"], "role": user["role"]})
    response.set_cookie("token", token, httponly=True, max_age=86400, samesite="lax")
    return response


@app.post("/api/auth/logout")
async def api_logout():
    """登出, 清除Cookie"""
    response = JSONResponse({"ok": True})
    response.delete_cookie("token")
    return response


@app.get("/api/auth/me")
async def get_me(request: Request):
    """获取当前登录用户信息"""
    token = request.cookies.get("token") or request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        raise HTTPException(401, "未登录")
    payload = verify_jwt(token)
    if not payload:
        raise HTTPException(401, "登录已过期")
    return {"username": payload.get("username"), "role": payload.get("role")}


# ============================================================
# 用户管理 API (管理员)
# ============================================================

@app.post("/api/admin/users")
async def admin_create_user(request: Request, admin: dict = Depends(require_admin)):
    """创建API用户"""
    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "").strip()
    role = body.get("role", "normal").strip()
    balance = float(body.get("balance", 100.0))
    quota_per_day = int(body.get("quota_per_day", 500))
    quota_per_month = int(body.get("quota_per_month", 5000))

    if not username or not password:
        raise HTTPException(400, "用户名和密码不能为空")
    if len(username) > 50:
        raise HTTPException(400, "用户名过长")
    if len(password) < 6:
        raise HTTPException(400, "密码至少6位")
    if role not in ("admin", "normal"):
        raise HTTPException(400, "角色只能是 admin 或 normal")

    db = await get_db()
    try:
        # 检查用户名重复
        cursor = await db.execute("SELECT id FROM api_users WHERE username = ?", (username,))
        if await cursor.fetchone():
            raise HTTPException(409, f"用户名 '{username}' 已存在")

        cursor = await db.execute(
            "INSERT INTO api_users (username, password_hash, role, balance, quota_per_day, quota_per_month) VALUES (?, ?, ?, ?, ?, ?)",
            (username, hash_password(password), role, balance, quota_per_day, quota_per_month),
        )
        await db.commit()
        return {"ok": True, "user_id": cursor.lastrowid, "username": username}
    finally:
        await db.close()


@app.get("/api/admin/users")
async def admin_list_users(admin: dict = Depends(require_admin)):
    """列出所有用户"""
    db = await get_db()
    try:
        # 列出所有 API 用户
        cursor = await db.execute(
            "SELECT id, username, role, balance, quota_per_day, quota_per_month, created_at FROM api_users ORDER BY id"
        )
        rows = await cursor.fetchall()
        return {
            "users": [
                {
                    "id": row["id"],
                    "username": row["username"],
                    "role": row["role"],
                    "balance": round(float(row["balance"]), 2),
                    "quota_per_day": row["quota_per_day"],
                    "quota_per_month": row["quota_per_month"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]
        }
    finally:
        await db.close()


@app.put("/api/admin/users/{user_id}")
async def admin_update_user(user_id: int, request: Request, admin: dict = Depends(require_admin)):
    """更新用户信息（密码/余额/配额）"""
    body = await request.json()
    db = await get_db()
    try:
        # 检查用户存在
        cursor = await db.execute("SELECT role FROM api_users WHERE id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "用户不存在")

        updates = []
        params = []
        new_password_hash = None

        if "password" in body:
            password = body["password"].strip()
            if len(password) < 6:
                raise HTTPException(400, "密码至少6位")
            updates.append("password_hash = ?")
            new_password_hash = hash_password(password)
            params.append(new_password_hash)

        if "balance" in body:
            updates.append("balance = ?")
            params.append(float(body["balance"]))

        if "quota_per_day" in body:
            updates.append("quota_per_day = ?")
            params.append(int(body["quota_per_day"]))

        if "quota_per_month" in body:
            updates.append("quota_per_month = ?")
            params.append(int(body["quota_per_month"]))

        if "role" in body:
            new_role = body["role"].strip()
            if new_role not in ("admin", "normal"):
                raise HTTPException(400, "角色只能是 admin 或 normal")
            updates.append("role = ?")
            params.append(new_role)

        if not updates:
            raise HTTPException(400, "没有要更新的字段")

        params.append(user_id)
        await db.execute(f"UPDATE api_users SET {', '.join(updates)} WHERE id = ?", params)
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: int, admin: dict = Depends(require_admin)):
    """删除用户"""
    db = await get_db()
    try:
        # 不允许删除管理员
        cursor = await db.execute("SELECT role FROM api_users WHERE id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "用户不存在")
        if row["role"] == "admin":
            raise HTTPException(400, "不能删除管理员账户")

        await db.execute("DELETE FROM api_users WHERE id = ?", (user_id,))
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


@app.post("/api/admin/users/{user_id}/keys")
async def admin_create_key(user_id: int, request: Request, admin: dict = Depends(require_admin)):
    """为用户创建API Key"""
    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    quota_per_day = int(body.get("quota_per_day", 100))
    quota_per_month = int(body.get("quota_per_month", 3000))
    price_per_call = float(body.get("price_per_call", 0.01))

    # 检查用户存在
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM api_users WHERE id = ?", (user_id,))
        if not await cursor.fetchone():
            raise HTTPException(404, "用户不存在")

        key_info = await create_api_key(
            user_id,
            quota_per_day=quota_per_day,
            quota_per_month=quota_per_month,
            price_per_call=price_per_call,
        )
        return {"ok": True, **key_info}
    finally:
        await db.close()


@app.get("/api/admin/users/{user_id}/keys")
async def admin_list_user_keys(user_id: int, admin: dict = Depends(require_admin)):
    """查看用户的所有API Key"""
    keys = await get_user_keys(user_id)
    return {"user_id": user_id, "keys": keys}


@app.delete("/api/admin/keys/{key_id}")
async def admin_delete_key(key_id: int, admin: dict = Depends(require_admin)):
    """禁用API Key"""
    ok = await revoke_api_key(key_id)
    if not ok:
        raise HTTPException(404, "Key 不存在")
    return {"ok": True}


@app.get("/api/admin/usage")
async def admin_usage_summary(days: int = Query(30, ge=1, le=365), admin: dict = Depends(require_admin)):
    """所有用户用量统计"""
    return await get_all_usage_summary()


@app.get("/api/admin/usage/{user_id}")
async def admin_user_usage(user_id: int, days: int = Query(30, ge=1, le=365), admin: dict = Depends(require_admin)):
    """某用户用量统计"""
    return await get_user_usage(user_id, days)


# ============================================================
# 搜索 API (需要API Key鉴权)
# ============================================================

@app.post("/api/search")
async def api_search_post(
    req: SearchRequest,
    request: Request,
    api_key_info: dict = Depends(require_api_key),
):
    """执行搜索 (POST) - 需要 X-API-Key"""
    # 输入长度限制
    if len(req.query) > 500:
        raise HTTPException(400, "查询内容过长，最长500字")

    # 检查配额
    if not await check_quota(api_key_info["key_id"]):
        raise HTTPException(429, "已达到日调用配额限制")

    # 检查余额
    balance = await check_balance(api_key_info["user_id"])
    if balance <= 0:
        raise HTTPException(402, "账户余额不足")

    # 执行搜索
    from core.aggregator import search_aggregator as agg
    start = time.time()
    result: SearchResponse = await agg.search(
        query=req.query,
        channels=req.channels,
        max_results=req.max_results,
    )
    latency_ms = (time.time() - start) * 1000

    # 记录用量和扣费
    cost = api_key_info["price_per_call"]
    used_channels = ",".join(result.channels_used) if result.channels_used else ""
    await record_usage(
        api_key_id=api_key_info["key_id"],
        user_id=api_key_info["user_id"],
        query=req.query[:500],
        channels=used_channels,
        results_count=result.total,
        latency_ms=latency_ms,
        cost=cost,
    )
    await deduct_balance(api_key_info["user_id"], cost)

    return result.model_dump()


@app.get("/api/search")
async def api_search_get(
    q: str = Query(..., description="搜索关键词"),
    channels: str | None = Query(None, description="渠道列表，逗号分隔"),
    max_results: int = Query(20, ge=1, le=100),
    request: Request = None,
    api_key_info: dict = Depends(require_api_key),
):
    """简易搜索 (GET) - 需要 X-API-Key"""
    # 输入长度限制
    if len(q) > 500:
        raise HTTPException(400, "查询内容过长，最长500字")

    # 检查配额
    if not await check_quota(api_key_info["key_id"]):
        raise HTTPException(429, "已达到日调用配额限制")

    # 检查余额
    balance = await check_balance(api_key_info["user_id"])
    if balance <= 0:
        raise HTTPException(402, "账户余额不足")

    # 执行搜索
    from core.aggregator import search_aggregator as agg
    ch_list = channels.split(",") if channels else None
    start = time.time()
    result: SearchResponse = await agg.search(
        query=q,
        channels=ch_list,
        max_results=max_results,
    )
    latency_ms = (time.time() - start) * 1000

    # 记录用量和扣费
    cost = api_key_info["price_per_call"]
    used_channels = ",".join(result.channels_used) if result.channels_used else ""
    await record_usage(
        api_key_id=api_key_info["key_id"],
        user_id=api_key_info["user_id"],
        query=q[:500],
        channels=used_channels,
        results_count=result.total,
        latency_ms=latency_ms,
        cost=cost,
    )
    await deduct_balance(api_key_info["user_id"], cost)

    return result.model_dump()


# ============================================================
# 渠道管理 API
# ============================================================

@app.get("/api/channels")
async def list_channels(admin: dict = Depends(require_admin)):
    """获取所有渠道列表及状态"""
    cm = _cm()
    channels = cm.get_all_channels()
    statuses = cm.get_all_channel_status()
    status_map = {s.name: s for s in statuses}

    result = []
    for name, ch in channels.items():
        st = status_map.get(name)
        result.append({
            "name": ch.name,
            "display_name": ch.display_name,
            "enabled": ch.enabled,
            "priority": ch.priority,
            "timeout": ch.timeout,
            "method": ch.method,
            "url": ch.url,
            "healthy": st.healthy if st else True,
            "keys_count": len(st.keys) if st else 0,
            "calls_today": st.calls_today if st else 0,
            "avg_latency_ms": st.avg_latency_ms if st else 0,
            "error_rate": st.error_rate if st else 0,
        })
    return {"channels": result}


@app.get("/api/channels/{name}")
async def get_channel(name: str, admin: dict = Depends(require_admin)):
    """获取单个渠道详情"""
    cm = _cm()
    ch = cm.get_channel(name)
    if ch is None:
        raise HTTPException(status_code=404, detail=f"渠道 {name} 不存在")
    return ch.model_dump()


@app.put("/api/channels/{name}")
async def update_channel(name: str, req: ChannelUpdateRequest, admin: dict = Depends(require_admin)):
    """更新渠道配置"""
    cm = _cm()
    updates = req.model_dump(exclude_unset=True)
    ch = cm.update_channel(name, updates)
    if ch is None:
        raise HTTPException(status_code=404, detail=f"渠道 {name} 不存在")
    # 如果 enabled 状态改变，同步更新 key_manager
    if "enabled" in updates:
        key_manager.init_channel(name, cm.get_keys(name))
    return {"ok": True, "channel": ch.model_dump()}


@app.post("/api/channels")
async def create_channel(request: Request, admin: dict = Depends(require_admin)):
    """创建新渠道"""
    body = await request.json()
    name = body.get("name", "").strip()
    display_name = body.get("display_name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="渠道名不能为空")
    if not name.isalnum():
        raise HTTPException(status_code=400, detail="渠道名只能包含字母和数字")
    cm = _cm()
    ch = cm.create_channel(name, display_name)
    if ch is None:
        raise HTTPException(status_code=409, detail=f"渠道 {name} 已存在")
    return {"ok": True, "channel": ch.model_dump()}


@app.delete("/api/channels/{name}")
async def delete_channel(name: str, admin: dict = Depends(require_admin)):
    """删除渠道"""
    cm = _cm()
    if not cm.delete_channel(name):
        raise HTTPException(status_code=404, detail=f"渠道 {name} 不存在")
    return {"ok": True}


@app.post("/api/channels/{name}/test")
async def test_channel(name: str, request: Request, admin: dict = Depends(require_admin)):
    """测试单个渠道的搜索功能（绕过计费和频率限制）"""
    import httpx as _httpx
    from core.aggregator import SearchAggregator

    cm = _cm()
    ch = cm.get_channel(name)
    if ch is None:
        raise HTTPException(status_code=404, detail=f"渠道 {name} 不存在")

    try:
        body = await request.json()
    except Exception:
        body = {}
    query = body.get("query", "artificial intelligence")
    key_index = body.get("key_index")  # 可选，指定 key 索引

    # 获取可用 key
    keys = cm.get_keys(name)
    enabled_keys = [k for k in keys if k.enabled]
    if not enabled_keys:
        raise HTTPException(status_code=400, detail="该渠道没有可用的 API Key")

    if key_index is not None and 0 <= key_index < len(enabled_keys):
        api_key = enabled_keys[key_index].key
    else:
        api_key = enabled_keys[0].key

    # 构建请求（复用 aggregator 的逻辑）
    agg = SearchAggregator()
    try:
        headers = agg._build_headers(ch, api_key)
        url, params, body_data = agg._build_request(ch, query)

        start = time.time()
        async with _httpx.AsyncClient(follow_redirects=True, timeout=_httpx.Timeout(30.0)) as client:
            if ch.method.upper() == "POST":
                resp = await client.post(url, headers=headers, json=body_data, params=params, timeout=ch.timeout)
            else:
                resp = await client.get(url, headers=headers, params=params, timeout=ch.timeout)
        latency_ms = (time.time() - start) * 1000

        status_code = resp.status_code
        raw_data = resp.json() if status_code < 400 else None

        # 尝试解析结果
        parsed_results = []
        if raw_data:
            try:
                results = agg._parse_response(ch, raw_data)
                parsed_results = [r.model_dump() for r in results]
            except Exception:
                pass

        return {
            "ok": status_code < 400,
            "status_code": status_code,
            "latency_ms": round(latency_ms, 2),
            "results_count": len(parsed_results),
            "results": parsed_results,
            "raw_response": raw_data,
            "error": resp.text[:500] if status_code >= 400 else None,
            "request": {
                "method": ch.method,
                "url": url,
                "params": params,
                "body": body_data if ch.method.upper() == "POST" else None,
            },
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("渠道测试失败: %s", name)
        return {
            "ok": False,
            "error": f"请求失败: {e}",
        }


# ============================================================
# Key 管理 API
# ============================================================

@app.get("/api/channels/{name}/keys")
async def list_keys(name: str, admin: dict = Depends(require_admin)):
    """获取渠道的所有 key 状态"""
    cm = _cm()
    if cm.get_channel(name) is None:
        raise HTTPException(status_code=404, detail=f"渠道 {name} 不存在")
    keys = cm.get_keys(name)
    return {
        "channel": name,
        "keys": [
            {
                "index": i,
                "key_masked": k.key[:4] + "****" + k.key[-4:] if len(k.key) > 8 else "****",
                "enabled": k.enabled,
                "labels": k.labels,
            }
            for i, k in enumerate(keys)
        ],
    }


@app.post("/api/channels/{name}/keys")
async def add_key(name: str, req: KeyAddRequest, admin: dict = Depends(require_admin)):
    """添加新 key"""
    cm = _cm()
    try:
        info = cm.add_key(name, req.key, req.labels)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"渠道 {name} 不存在")
    # 同步更新 key_manager
    key_manager.init_channel(name, cm.get_keys(name))
    return {"ok": True, "key": info.model_dump()}


@app.delete("/api/channels/{name}/keys")
async def delete_key(name: str, request: Request, admin: dict = Depends(require_admin)):
    """删除 key (body: {"index": N} 或 {"key": "xxx"})"""
    body = await request.json()
    cm = _cm()
    keys = cm.get_keys(name)
    # 支持按 index 删除（推荐）或按 key 值删除
    idx = body.get("index")
    key_to_delete = body.get("key", "")
    if idx is not None:
        if not (0 <= idx < len(keys)):
            raise HTTPException(status_code=404, detail="key index 不存在")
        key_to_delete = keys[idx].key
    if not key_to_delete:
        raise HTTPException(status_code=400, detail="缺少 index 或 key 字段")
    cm = _cm()
    removed = cm.remove_key(name, key_to_delete)
    if not removed:
        raise HTTPException(status_code=404, detail="key 不存在")
    # 同步更新 key_manager
    key_manager.init_channel(name, cm.get_keys(name))
    return {"ok": True}


# ============================================================
# 监控 API
# ============================================================

@app.get("/api/monitor/stats")
async def monitor_stats(admin: dict = Depends(require_admin)):
    """所有渠道统计（含配额使用情况）"""
    cm = _cm()
    statuses = cm.get_all_channel_status()
    # 注入配额使用数据
    from core.rate_limiter import rate_limiter
    quota_usage = rate_limiter.get_all_quota_usage()
    channels = []
    for s in statuses:
        d = s.model_dump()
        q = quota_usage.get(s.name, {})
        d["quota"] = q
        channels.append(d)
    return {"channels": channels}


@app.get("/api/monitor/health")
async def monitor_health(admin: dict = Depends(require_admin)):
    """健康状态"""
    cm = _cm()
    return cm.get_health()


@app.get("/api/monitor/quota")
async def monitor_quota(admin: dict = Depends(require_admin)):
    """所有渠道的配额使用情况"""
    from core.rate_limiter import rate_limiter
    return rate_limiter.get_all_quota_usage()


@app.get("/api/monitor/user-stats")
async def monitor_user_stats(days: int = Query(30, ge=1, le=365), admin: dict = Depends(require_admin)):
    """用户调用统计（用于用户监控面板）"""
    db = await get_db()
    try:
        # 每个用户的总调用、总花费、今日调用
        cursor = await db.execute(
            """SELECT
                 au.id as user_id,
                 au.username,
                 au.balance,
                 au.quota_per_day,
                 au.quota_per_month,
                 COALESCE(SUM(CASE WHEN ul.created_at >= DATE('now') THEN 1 ELSE 0 END), 0) as calls_today,
                 COALESCE(SUM(CASE WHEN ul.created_at >= DATE('now', '-1 month') THEN 1 ELSE 0 END), 0) as calls_month,
                 COUNT(ul.id) as total_calls,
                 COALESCE(SUM(ul.cost), 0) as total_cost,
                 COALESCE(SUM(CASE WHEN ul.status != 'ok' THEN 1 ELSE 0 END), 0) as total_errors
               FROM api_users au
               LEFT JOIN usage_logs ul ON au.id = ul.user_id
               GROUP BY au.id
               ORDER BY total_cost DESC"""
        )
        rows = await cursor.fetchall()

        users = []
        for row in rows:
            # 获取该用户最近N天的每日明细
            daily_cursor = await db.execute(
                """SELECT DATE(created_at) as date,
                          COUNT(*) as calls,
                          COALESCE(SUM(cost), 0) as cost,
                          SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END) as errors
                   FROM usage_logs
                   WHERE user_id = ? AND created_at >= DATE('now', ?)
                   GROUP BY DATE(created_at)
                   ORDER BY date DESC""",
                (row["user_id"], f"-{days} days"),
            )
            daily_rows = await daily_cursor.fetchall()
            daily = [
                {"date": d["date"], "calls": d["calls"], "cost": float(d["cost"]), "errors": d["errors"]}
                for d in daily_rows
            ]

            users.append({
                "user_id": row["user_id"],
                "username": row["username"],
                "balance": float(row["balance"]),
                "quota_per_day": row["quota_per_day"],
                "quota_per_month": row["quota_per_month"],
                "calls_today": row["calls_today"],
                "calls_month": row["calls_month"],
                "total_calls": row["total_calls"],
                "total_cost": float(row["total_cost"]),
                "total_errors": row["total_errors"],
                "daily": daily,
            })

        return {
            "total_users": len(users),
            "users": users,
        }
    finally:
        await db.close()

@app.get("/api/logs")
async def get_usage_logs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: dict = Depends(require_login),
):
    """调用日志查询（admin 看所有，normal 看自己的）"""
    db = await get_db()
    try:
        # 构建查询条件
        if user["role"] == "admin":
            where_clause = ""
            params: list[Any] = []
        else:
            # normal 用户只能看自己的日志，通过 api_keys 关联找到 user_id
            where_clause = "WHERE ul.user_id = (SELECT id FROM api_users WHERE username = ?)"
            params = [user["username"]]

        # 总条数
        count_sql = f"SELECT COUNT(*) as cnt FROM usage_logs ul {where_clause}"
        cursor = await db.execute(count_sql, params)
        total = (await cursor.fetchone())["cnt"]

        # 分页查询
        offset = (page - 1) * page_size
        query_sql = f"""
            SELECT ul.id, ul.api_key_id, ul.user_id, au.username,
                   ul.query, ul.channels, ul.results_count, ul.latency_ms,
                   ul.cost, ul.status, ul.error_msg, ul.ip_address, ul.created_at
            FROM usage_logs ul
            LEFT JOIN api_users au ON ul.user_id = au.id
            {where_clause}
            ORDER BY ul.id DESC
            LIMIT ? OFFSET ?
        """
        cursor = await db.execute(query_sql, params + [page_size, offset])
        rows = await cursor.fetchall()

        logs = [
            {
                "id": row["id"],
                "api_key_id": row["api_key_id"],
                "user_id": row["user_id"],
                "username": row["username"],
                "query": row["query"],
                "channels": row["channels"],
                "results_count": row["results_count"],
                "latency_ms": round(float(row["latency_ms"]), 2),
                "cost": round(float(row["cost"]), 4),
                "status": row["status"],
                "error_msg": row["error_msg"],
                "ip_address": row["ip_address"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

        return {
            "logs": logs,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
        }
    finally:
        await db.close()


@app.get("/api/logs/export")
async def export_usage_logs(
    request: Request,
    days: int = Query(30, ge=1, le=365),
    user: dict = Depends(require_login),
):
    """导出调用日志（CSV 格式）"""
    db = await get_db()
    try:
        if user["role"] == "admin":
            where_clause = ""
            params: list[Any] = []
        else:
            where_clause = "WHERE ul.user_id = (SELECT id FROM api_users WHERE username = ?)"
            params = [user["username"]]

        if where_clause:
            where_clause += f" AND ul.created_at >= DATE('now', '-{days} days')"
        else:
            where_clause = f"WHERE ul.created_at >= DATE('now', '-{days} days')"

        query_sql = f"""
            SELECT ul.id, au.username, ul.query, ul.channels,
                   ul.results_count, ul.latency_ms, ul.cost,
                   ul.status, ul.error_msg, ul.created_at
            FROM usage_logs ul
            LEFT JOIN api_users au ON ul.user_id = au.id
            {where_clause}
            ORDER BY ul.id DESC
        """
        cursor = await db.execute(query_sql, params)
        rows = await cursor.fetchall()

        import csv
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "用户", "查询", "渠道", "结果数", "延迟(ms)", "费用", "状态", "错误", "时间"])
        for row in rows:
            writer.writerow([
                row["id"], row["username"], row["query"], row["channels"],
                row["results_count"], round(float(row["latency_ms"]), 2),
                round(float(row["cost"]), 4), row["status"],
                row["error_msg"] or "", row["created_at"],
            ])

        from fastapi.responses import StreamingResponse
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=usage_logs.csv"},
        )
    finally:
        await db.close()


@app.get("/api/config")
async def get_config(admin: dict = Depends(require_admin)):
    """全局配置"""
    cm = _cm()
    return cm.get_settings()


@app.put("/api/config")
async def update_config(request: Request, admin: dict = Depends(require_admin)):
    """更新全局配置"""
    body = await request.json()
    cm = _cm()
    updated = cm.update_settings(body)
    return {"ok": True, "config": updated}


@app.post("/api/config/reload")
async def reload_config(admin: dict = Depends(require_admin)):
    """热重载配置"""
    cm = _cm()
    cm.reload()
    # 同步更新 key_manager（渠道 Key 变更后运行时必须重载）
    channel_keys = {name: cm.get_keys(name) for name in cm.get_all_channels()}
    key_manager.init_all(channel_keys)
    # 同步更新 rate_limiter 配额计数器
    from core.rate_limiter import rate_limiter
    await rate_limiter.init_from_db()
    return {"ok": True, "message": "配置已重载"}
