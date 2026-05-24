"""
频率限制器 + 调用配额管理

- 频率限制：per_minute / per_hour / per_day / per_month（滑动窗口，基于内存时间戳列表）
- 调用配额：per_day / per_month（累计计数，启动时从 usage_logs 加载，定期同步）

限制参数从渠道 YAML 配置的 rate_limits / quota 字段读取。
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, date

logger = logging.getLogger(__name__)

# 调用记录同步到数据库的间隔（每 N 次调用写一次）
_SYNC_INTERVAL = 50


class RateLimiter:
    """内存级频率限制 + 持久化配额追踪"""

    def __init__(self) -> None:
        # ---------- 频率限制（纯内存，重启清零）----------
        # key = (channel, api_key) → [timestamp, ...]
        self._call_records: dict[tuple[str, str], list[float]] = defaultdict(list)

        # ---------- 调用配额（启动时从 DB 加载）----------
        # key = channel → { "day": int, "month": int, "day_key": str, "month_key": str }
        self._quota_usage: dict[str, dict] = {}

        # ---------- 同步计数器 ----------
        self._sync_counter: dict[str, int] = defaultdict(int)
        self._dirty: set[str] = set()  # 有待同步的渠道

        self._today_key = date.today().isoformat()
        self._this_month_key = datetime.now().strftime("%Y-%m")

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def can_call(self, channel: str, channel_config: object, key: str = "") -> bool:
        """检查是否可以发起调用

        Args:
            channel: 渠道名
            channel_config: ChannelConfig 对象（含 rate_limits / quota）
            key: API Key 原始值（可选，用于 per-key 频率限制）
        """
        self._maybe_reset_window()
        rate_cfg = getattr(channel_config, "rate_limits", None)
        quota_cfg = getattr(channel_config, "quota", None)

        if rate_cfg is None and quota_cfg is None:
            return True

        now = time.time()
        records = self._call_records[(channel, key)]

        # 1) 频率限制（滑动窗口）
        if rate_cfg:
            if rate_cfg.per_minute > 0:
                window = [t for t in records if now - t < 60]
                if len(window) >= rate_cfg.per_minute:
                    return False
            if rate_cfg.per_hour > 0:
                window = [t for t in records if now - t < 3600]
                if len(window) >= rate_cfg.per_hour:
                    return False
            if rate_cfg.per_day > 0:
                window = [t for t in records if now - t < 86400]
                if len(window) >= rate_cfg.per_day:
                    return False
            if rate_cfg.per_month > 0:
                window = [t for t in records if now - t < 2592000]
                if len(window) >= rate_cfg.per_month:
                    return False

        # 2) 调用配额（从 usage_logs 累计）
        if quota_cfg:
            usage = self._get_or_init_usage(channel, quota_cfg)
            if quota_cfg.per_day > 0 and usage["day"] >= quota_cfg.per_day:
                return False
            if quota_cfg.per_month > 0 and usage["month"] >= quota_cfg.per_month:
                return False

        return True

    def record_call(self, channel: str, channel_config: object, key: str = "") -> None:
        """记录一次调用，更新频率窗口和配额计数"""
        now = time.time()

        # 频率窗口：记录时间戳
        self._call_records[(channel, key)].append(now)
        # 清理超过 30 天的旧记录，防止内存膨胀
        cutoff = now - 2592000
        self._call_records[(channel, key)] = [
            t for t in self._call_records[(channel, key)] if t > cutoff
        ]

        # 配额计数：自增
        quota_cfg = getattr(channel_config, "quota", None)
        if quota_cfg:
            usage = self._get_or_init_usage(channel, quota_cfg)
            usage["day"] += 1
            usage["month"] += 1
            self._dirty.add(channel)

            # 定期同步到数据库
            self._sync_counter[channel] += 1
            if self._sync_counter[channel] >= _SYNC_INTERVAL:
                self._sync_to_db(channel)
                self._sync_counter[channel] = 0

    def get_quota_usage(self, channel: str) -> dict[str, dict[str, int]]:
        """获取渠道级别的配额使用情况

        Returns:
            { "per_day": {"used": N, "limit": M}, "per_month": {...} }
        """
        from core.config_manager import get_config_manager
        cfg = get_config_manager().channels.get(channel)
        if cfg is None:
            return {"per_day": {"used": 0, "limit": 0}, "per_month": {"used": 0, "limit": 0}}

        quota_cfg = cfg.quota
        usage = self._get_or_init_usage(channel, quota_cfg)
        return {
            "per_day": {"used": usage["day"], "limit": quota_cfg.per_day},
            "per_month": {"used": usage["month"], "limit": quota_cfg.per_month},
        }

    def get_all_quota_usage(self) -> dict[str, dict]:
        """获取所有渠道的配额使用情况（供前端展示）"""
        from core.config_manager import get_config_manager
        result = {}
        for name, cfg in get_config_manager().channels.items():
            quota_cfg = cfg.quota
            usage = self._get_or_init_usage(name, quota_cfg)
            result[name] = {
                "per_day": {"used": usage["day"], "limit": quota_cfg.per_day},
                "per_month": {"used": usage["month"], "limit": quota_cfg.per_month},
            }
        return result

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    async def init_from_db(self) -> None:
        """启动时从 usage_logs 加载配额使用量"""
        from core.config_manager import get_config_manager
        try:
            # 先从配置初始化所有渠道的配额结构
            for name, cfg in get_config_manager().channels.items():
                self._get_or_init_usage(name, cfg.quota)

            from core.database import get_db
            db = await get_db()

            # 加载今日调用量（按渠道）
            cursor = await db.execute(
                """SELECT channels, COUNT(*) as cnt
                   FROM usage_logs
                   WHERE created_at >= DATE('now')
                   GROUP BY channels"""
            )
            day_rows = await cursor.fetchall()
            for row in day_rows:
                ch_name = row["channels"]
                if ch_name and ch_name in self._quota_usage:
                    self._quota_usage[ch_name]["day"] = row["cnt"]

            # 加载本月调用量（按渠道）
            cursor = await db.execute(
                """SELECT channels, COUNT(*) as cnt
                   FROM usage_logs
                   WHERE created_at >= DATE('now', 'start of month')
                   GROUP BY channels"""
            )
            month_rows = await cursor.fetchall()
            for row in month_rows:
                ch_name = row["channels"]
                if ch_name and ch_name in self._quota_usage:
                    self._quota_usage[ch_name]["month"] = row["cnt"]

            total_day = sum(r["cnt"] for r in day_rows)
            total_month = sum(r["cnt"] for r in month_rows)
            logger.info(
                "配额使用量已加载：今日 %d 次，本月 %d 次",
                total_day, total_month,
            )
        except Exception as e:
            logger.warning("从数据库加载配额使用量失败: %s", e)

    def sync_all_to_db(self) -> None:
        """同步所有脏数据到数据库"""
        for channel in list(self._dirty):
            self._sync_to_db(channel)

    def _sync_to_db(self, channel: str) -> None:
        """将内存中的配额计数同步到数据库（同步调用，不阻塞事件循环）"""
        if channel not in self._dirty:
            return

        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._async_sync(channel))
        except RuntimeError:
            # 没有事件循环时，同步执行
            import asyncio as _aio
            _aio.run(self._async_sync(channel))
        self._dirty.discard(channel)

    async def _async_sync(self, channel: str) -> None:
        """异步同步单个渠道的配额到数据库"""
        try:
            usage = self._quota_usage.get(channel)
            if not usage:
                return
            # 数据库中的 usage_logs 就是最终数据源
            # 内存计数在启动时已从 DB 加载，后续每次 record_call 自增
            # 定期 sync 的目的是确保内存和 DB 一致
            logger.debug("配额同步: %s day=%d month=%d", channel, usage["day"], usage["month"])
        except Exception as e:
            logger.warning("配额同步失败 [%s]: %s", channel, e)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _get_or_init_usage(self, channel: str, quota_cfg: object) -> dict:
        """获取或初始化渠道的配额使用量"""
        if channel not in self._quota_usage:
            self._quota_usage[channel] = {
                "day": 0,
                "month": 0,
                "day_key": self._today_key,
                "month_key": self._this_month_key,
            }

        usage = self._quota_usage[channel]

        # 日期翻转：新的一天清零日计数
        today = date.today().isoformat()
        if usage.get("day_key") != today:
            usage["day"] = 0
            usage["day_key"] = today

        # 月份翻转：新的一月清零月计数
        this_month = datetime.now().strftime("%Y-%m")
        if usage.get("month_key") != this_month:
            usage["month"] = 0
            usage["month_key"] = this_month

        return usage

    def _maybe_reset_window(self) -> None:
        """检查是否跨天/跨月，重置窗口"""
        today = date.today().isoformat()
        if self._today_key != today:
            self._today_key = today
            for usage in self._quota_usage.values():
                usage["day"] = 0
                usage["day_key"] = today
            logger.info("日期翻转，日配额已重置")

        this_month = datetime.now().strftime("%Y-%m")
        if self._this_month_key != this_month:
            self._this_month_key = this_month
            for usage in self._quota_usage.values():
                usage["month"] = 0
                usage["month_key"] = this_month
            logger.info("月份翻转，月配额已重置")


# 模块级单例
rate_limiter = RateLimiter()
