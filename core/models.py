"""数据模型定义 - 所有模块共享的数据结构"""
from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field
from datetime import datetime


# ============================================================
# 搜索结果标准格式
# ============================================================
# 固定字段: content(必填), url, title, image, datetime
# 各渠道通过 response.mapping 配置映射关系

class SearchResult(BaseModel):
    """统一搜索结果 - 所有渠道都映射为此格式"""
    content: str                               # 摘要/正文片段（必填）
    url: str = ""
    title: str = ""
    image: str | None = None
    datetime: str | None = None
    source: str = ""                           # 来源渠道名（内部字段，不返回给调用方）
    raw: dict[str, Any] = Field(default_factory=dict)  # 原始响应（内部字段，不返回给调用方）


class SearchResponse(BaseModel):
    """搜索响应 - 聚合后的完整结果"""
    query: str
    results: list[SearchResult] = Field(default_factory=list)
    total: int = 0
    channels_used: list[str] = Field(default_factory=list)
    channels_failed: list[str] = Field(default_factory=list)
    latency_ms: float = 0
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

    def model_dump(self, **kwargs):
        d = super().model_dump(**kwargs)
        # 排除顶层内部字段
        for key in ("channels_used", "channels_failed", "latency_ms"):
            d.pop(key, None)
        # 排除每个 result 中的内部字段 source 和 raw
        for r in d.get("results", []):
            r.pop("source", None)
            r.pop("raw", None)
        return d


# ============================================================
# 渠道配置模型
# ============================================================
class AuthConfig(BaseModel):
    type: str = "header"            # header / query_param
    header_name: str = "Authorization"
    header_prefix: str = ""
    query_param: str = "api_key"


class APIKeyInfo(BaseModel):
    key: str
    enabled: bool = True
    labels: list[str] = Field(default_factory=list)


class RateLimitConfig(BaseModel):
    per_minute: int = 10
    per_hour: int = 100
    per_day: int = 500
    per_month: int = 10000


class QuotaConfig(BaseModel):
    per_day: int = 500
    per_month: int = 10000


class ChannelConfig(BaseModel):
    """单个搜索渠道的完整配置"""
    name: str
    display_name: str = ""
    enabled: bool = True
    method: str = "GET"
    url: str = ""
    timeout: int = 5
    auth: AuthConfig = Field(default_factory=AuthConfig)
    request: dict[str, Any] = Field(default_factory=dict)
    response: dict[str, Any] = Field(default_factory=dict)
    rate_limits: RateLimitConfig = Field(default_factory=RateLimitConfig)
    quota: QuotaConfig = Field(default_factory=QuotaConfig)
    priority: int = 100             # 数字越小优先级越高
    max_retries: int = 1


# ============================================================
# Key 运行时状态
# ============================================================
class KeyStatus(BaseModel):
    """单个 API Key 的运行时状态"""
    key_masked: str                 # 脱敏后的 key
    enabled: bool = True
    failure_count: int = 0
    last_error: str | None = None
    last_used: str | None = None
    total_calls: int = 0
    is_healthy: bool = True


class ChannelStatus(BaseModel):
    """单个渠道的运行时状态"""
    name: str
    enabled: bool = True
    healthy: bool = True
    keys: list[KeyStatus] = Field(default_factory=list)
    calls_today: int = 0
    calls_this_month: int = 0
    calls_per_minute: int = 0
    calls_per_hour: int = 0
    avg_latency_ms: float = 0
    error_rate: float = 0
    last_error: str | None = None
    last_error_time: str | None = None


# ============================================================
# API 请求/响应
# ============================================================
class SearchRequest(BaseModel):
    query: str
    channels: list[str] | None = None  # 指定渠道，None 则用全部
    max_results: int = Field(default=20, ge=1, le=100)


class ChannelUpdateRequest(BaseModel):
    """Web 管理界面 - 更新渠道配置（支持所有字段）"""
    display_name: str | None = None
    enabled: bool | None = None
    method: str | None = None
    url: str | None = None
    timeout: int | None = None
    priority: int | None = None
    max_retries: int | None = None
    auth: AuthConfig | None = None
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    rate_limits: RateLimitConfig | None = None
    quota: QuotaConfig | None = None


class KeyAddRequest(BaseModel):
    """Web 管理界面 - 添加 API Key"""
    key: str
    labels: list[str] = Field(default_factory=list)
