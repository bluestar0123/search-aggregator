"""搜索聚合器 - 对外统一入口，并发搜索多个渠道并聚合结果"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from core.key_manager import key_manager
from core.models import ChannelConfig, SearchResult, SearchResponse
from core.config_manager import get_config_manager
from core.rate_limiter import rate_limiter

logger = logging.getLogger(__name__)

# 复用 key_manager 的脱敏函数，避免重复实现
_mask_key = key_manager._mask_key


class SearchAggregator:
    """搜索聚合器 - 并发查询多渠道，聚合去重后返回统一结果"""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """延迟创建共享的异步 HTTP 客户端"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(30.0),
            )
        return self._client

    async def close(self) -> None:
        """关闭 HTTP 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        channels: list[str] | None = None,
        max_results: int = 20,
    ) -> SearchResponse:
        """统一搜索入口

        Args:
            query: 搜索关键词
            channels: 指定搜索的渠道列表，None 表示使用全部可用渠道
            max_results: 最大返回结果数

        Returns:
            SearchResponse: 聚合后的标准搜索响应
        """
        start_time = time.monotonic()
        config_mgr = get_config_manager()
        all_channels_dict = config_mgr.get_all_channels()

        # 筛选目标渠道
        if channels:
            target_names = set(channels)
            usable_list = [
                ch for name, ch in all_channels_dict.items() if name in target_names
            ]
        else:
            usable_list = list(all_channels_dict.values())

        # 按优先级排序（数字小优先级高）
        usable_list.sort(key=lambda c: c.priority)

        # 检查每个渠道是否可用，构建可执行列表
        usable: list[ChannelConfig] = []
        for ch in usable_list:
            if not ch.enabled:
                continue
            if not key_manager.has_healthy_key(ch.name):
                logger.debug("渠道 %s 无可用 key，跳过", ch.name)
                continue
            quota = rate_limiter.get_quota_usage(ch.name)
            day_quota = quota.get("per_day", {})
            if day_quota.get("limit", 0) > 0 and day_quota["used"] >= day_quota["limit"]:
                logger.debug("渠道 %s 日配额已满，跳过", ch.name)
                continue
            usable.append(ch)

        if not usable:
            elapsed = (time.monotonic() - start_time) * 1000
            return SearchResponse(
                query=query,
                results=[],
                total=0,
            )

        # 优先级依次尝试：按 priority 顺序搜，搜到结果就停，不够也不再请求
        all_results: list[SearchResult] = []
        channels_used: list[str] = []
        channels_failed: list[str] = []

        for ch in usable:
            try:
                result = await self._search_channel(ch, query)
                if result is not None and len(result) > 0:
                    all_results.extend(result)
                    channels_used.append(ch.name)
                    break  # 搜到结果就停，不再请求其他渠道
                else:
                    channels_failed.append(ch.name)
            except Exception as e:
                channels_failed.append(ch.name)
                logger.error("渠道 %s 搜索异常: %s", ch.name, e)

        # 去重（按 URL 去重）
        seen_urls: set[str] = set()
        unique_results: list[SearchResult] = []
        for r in all_results:
            if r.url and r.url in seen_urls:
                continue
            seen_urls.add(r.url)
            unique_results.append(r)

        # 截断到 max_results
        final_results = unique_results[:max_results]

        elapsed = (time.monotonic() - start_time) * 1000
        return SearchResponse(
            query=query,
            results=final_results,
            total=len(final_results),
            channels_used=channels_used,
            channels_failed=channels_failed,
            latency_ms=round(elapsed, 2),
        )

    # ------------------------------------------------------------------
    # 单渠道搜索
    # ------------------------------------------------------------------

    async def _search_channel(
        self, channel: ChannelConfig, query: str
    ) -> list[SearchResult] | None:
        """对单个渠道发起搜索，支持重试和 key 切换

        Returns:
            搜索结果列表，失败返回 None
        """
        max_retries = channel.max_retries
        last_error: str | None = None

        for attempt in range(max_retries + 1):
            api_key_info = key_manager.get_key(channel.name)
            if api_key_info is None:
                logger.debug("渠道 %s 无可用 key (尝试 %d)", channel.name, attempt + 1)
                last_error = "no available key"
                continue

            # 检查频率限制
            if not rate_limiter.can_call(channel.name, channel, api_key_info.key):
                logger.debug("渠道 %s key %s 频率超限", channel.name, _mask_key(api_key_info.key))
                last_error = "rate limited"
                key_manager.report_failure(channel.name, api_key_info.key, "rate limited")
                continue

            try:
                results = await self._do_search(channel, api_key_info.key, query)
                rate_limiter.record_call(channel.name, channel, api_key_info.key)
                key_manager.report_success(channel.name, api_key_info.key)
                return results
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    "渠道 %s key %s 搜索失败 (尝试 %d/%d): %s",
                    channel.name, _mask_key(api_key_info.key),
                    attempt + 1, max_retries + 1, e,
                )
                key_manager.report_failure(channel.name, api_key_info.key, str(e))

        logger.error("渠道 %s 所有尝试均失败: %s", channel.name, last_error)
        return None

    async def _do_search(
        self, channel: ChannelConfig, api_key: str, query: str
    ) -> list[SearchResult]:
        """实际发起 HTTP 请求并解析响应"""
        client = await self._get_client()
        headers = self._build_headers(channel, api_key)
        url, params, body = self._build_request(channel, query)

        if channel.method.upper() == "POST":
            resp = await client.post(
                url,
                headers=headers,
                json=body,
                params=params,
                timeout=channel.timeout,
            )
        else:
            resp = await client.get(
                url,
                headers=headers,
                params=params,
                timeout=channel.timeout,
            )

        resp.raise_for_status()
        data = resp.json()
        return self._parse_response(channel, data)

    # ------------------------------------------------------------------
    # 请求构建
    # ------------------------------------------------------------------

    def _build_headers(self, channel: ChannelConfig, api_key: str) -> dict[str, str]:
        """构建认证 headers"""
        headers: dict[str, str] = {}
        auth = channel.auth
        if auth.type == "header":
            if auth.header_prefix:
                headers[auth.header_name] = f"{auth.header_prefix}{api_key}"
            else:
                headers[auth.header_name] = api_key
        return headers

    def _build_request(
        self, channel: ChannelConfig, query: str
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        """构建请求 URL、参数和 body"""
        url = channel.url
        params: dict[str, str] = {}
        body: dict[str, Any] = {}

        request_cfg = channel.request

        # POST 请求: 构建 body
        if channel.method.upper() == "POST":
            body_template = request_cfg.get("body_template", {})
            body = self._fill_template(body_template, query)
        else:
            # GET 请求: 构建 query params
            params_template = request_cfg.get("params_template", {})
            params = {k: self._fill_value(v, query) for k, v in params_template.items()}

        return url, params, body

    @staticmethod
    def _fill_template(template: dict[str, Any], query: str) -> dict[str, Any]:
        """递归替换模板中的 {query} 占位符"""
        result: dict[str, Any] = {}
        for k, v in template.items():
            if isinstance(v, str):
                result[k] = v.replace("{query}", query)
            elif isinstance(v, dict):
                result[k] = SearchAggregator._fill_template(v, query)
            else:
                result[k] = v
        return result

    @staticmethod
    def _fill_value(value: Any, query: str) -> Any:
        """替换单个值中的 {query} 占位符"""
        if isinstance(value, str):
            return value.replace("{query}", query)
        return value

    # ------------------------------------------------------------------
    # 响应解析
    # ------------------------------------------------------------------

    def _parse_response(self, channel: ChannelConfig, data: dict[str, Any]) -> list[SearchResult]:
        """将渠道 API 响应映射为标准 SearchResult

        response.mapping 格式: {标准字段: 渠道API字段路径}
        固定字段: content(必填), url, title, image, datetime
        """
        response_cfg = channel.response
        results_path = response_cfg.get("results_path", "results")
        mapping = response_cfg.get("mapping", {})

        # 提取结果列表
        raw_results = self._get_nested(data, results_path)
        if not isinstance(raw_results, list):
            return []

        results: list[SearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue

            # 提取 content（必填）
            content_field = mapping.get("content", "content")
            content_val = self._get_nested(item, content_field)
            if content_val is None:
                # 兼容 snippet 字段
                content_val = self._get_nested(item, "snippet") or self._get_nested(item, "content")
            if content_val is None:
                continue  # content 必填，无法映射则跳过
            if isinstance(content_val, list):
                content_val = content_val[0] if content_val else ""

            result = SearchResult(
                content=str(content_val),
                source=channel.name,
                raw=item,
            )

            # 映射可选字段
            for std_field in ("title", "url", "image", "datetime"):
                api_field = mapping.get(std_field)
                if api_field:
                    val = self._get_nested(item, api_field)
                    if val is not None:
                        setattr(result, std_field, str(val))

            results.append(result)

        return results

    @staticmethod
    def _get_nested(data: dict[str, Any], path: str) -> Any:
        """支持点号分隔的嵌套路径取值，如 'results.hits'"""
        parts = path.split(".")
        current = data
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current


# 模块级单例
search_aggregator = SearchAggregator()
