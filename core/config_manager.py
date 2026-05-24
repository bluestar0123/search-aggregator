"""配置管理器 - 加载和管理 settings.yaml + channels/*.yaml"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from core.models import (
    APIKeyInfo,
    AuthConfig,
    ChannelConfig,
    ChannelStatus,
    KeyStatus,
    QuotaConfig,
    RateLimitConfig,
)

# 项目根目录 (core/ 的上级)
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
SETTINGS_FILE = CONFIG_DIR / "settings.yaml"
CHANNELS_DIR = CONFIG_DIR / "channels"


class ConfigManager:
    """管理全局配置和渠道配置"""

    def __init__(self) -> None:
        self.settings: dict[str, Any] = {}
        self.channels: dict[str, ChannelConfig] = {}
        self.channel_raw: dict[str, dict[str, Any]] = {}  # 原始 yaml 数据

    def load(self) -> None:
        """加载所有配置文件"""
        self._load_settings()
        self._load_channels()

    def reload(self) -> None:
        """热重载配置"""
        self.load()

    # ---- 内部方法 ----

    def _load_settings(self) -> None:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                self.settings = yaml.safe_load(f) or {}
        else:
            self.settings = {}

    def _load_channels(self) -> None:
        self.channels.clear()
        self.channel_raw.clear()
        if not CHANNELS_DIR.exists():
            return
        for yaml_file in sorted(CHANNELS_DIR.glob("*.yaml")):
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}
                ch_data = raw.get("channel", {})
                name = ch_data.get("name", yaml_file.stem)
                auth_data = raw.get("auth", {})
                keys_data = auth_data.pop("keys", []) or []
                # 构建 ChannelConfig
                config = ChannelConfig(
                    name=name,
                    display_name=ch_data.get("display_name", name),
                    enabled=ch_data.get("enabled", True),
                    method=ch_data.get("method", "GET"),
                    url=ch_data.get("url", ""),
                    timeout=ch_data.get("timeout", 10),
                    auth=AuthConfig(**{k: v for k, v in auth_data.items() if k in AuthConfig.model_fields}),
                    request=raw.get("request", {}),
                    response=raw.get("response", {}),
                    rate_limits=RateLimitConfig(**{
                        k: v for k, v in (raw.get("rate_limits") or {}).items()
                        if k in RateLimitConfig.model_fields
                    }) if raw.get("rate_limits") else RateLimitConfig(),
                    quota=QuotaConfig(**{
                        k: v for k, v in (raw.get("quota") or {}).items()
                        if k in QuotaConfig.model_fields
                    }) if raw.get("quota") else QuotaConfig(),
                    priority=ch_data.get("priority", 100),
                    max_retries=ch_data.get("max_retries", 2),
                )
                self.channels[name] = config
                # 保存原始数据 (含 keys)
                self.channel_raw[name] = {
                    "config_raw": raw,
                    "keys": [
                        APIKeyInfo(**k) if isinstance(k, dict) else APIKeyInfo(key=str(k))
                        for k in keys_data
                    ],
                }
            except Exception as e:
                print(f"[config_manager] 加载 {yaml_file.name} 失败: {e}")

    # ---- 公开接口 ----

    def get_all_channels(self) -> dict[str, ChannelConfig]:
        return dict(self.channels)

    def get_channel(self, name: str) -> ChannelConfig | None:
        return self.channels.get(name)

    def update_channel(self, name: str, updates: dict[str, Any]) -> ChannelConfig | None:
        ch = self.channels.get(name)
        if ch is None:
            return None
        for key, value in updates.items():
            if value is not None and hasattr(ch, key):
                setattr(ch, key, value)
        # 持久化到 YAML
        self._save_channel_yaml(name)
        return ch

    def create_channel(self, name: str, display_name: str = "") -> ChannelConfig | None:
        """创建新渠道"""
        if name in self.channels:
            return None
        config = ChannelConfig(name=name, display_name=display_name or name)
        self.channels[name] = config
        self.channel_raw[name] = {"config_raw": {}, "keys": []}
        self._save_channel_yaml(name)
        return config

    def delete_channel(self, name: str) -> bool:
        """删除渠道"""
        if name not in self.channels:
            return False
        del self.channels[name]
        self.channel_raw.pop(name, None)
        yaml_file = CHANNELS_DIR / f"{name}.yaml"
        if yaml_file.exists():
            yaml_file.unlink()
        return True

    def _save_channel_yaml(self, name: str) -> None:
        """将渠道配置持久化到 YAML 文件"""
        ch = self.channels.get(name)
        if ch is None:
            return
        raw = self.channel_raw.get(name, {})
        keys_data = raw.get("keys", [])

        # 构建 YAML 结构
        data = {
            "channel": {
                "name": ch.name,
                "display_name": ch.display_name,
                "enabled": ch.enabled,
                "method": ch.method,
                "url": ch.url,
                "timeout": ch.timeout,
                "priority": ch.priority,
                "max_retries": ch.max_retries,
            },
            "auth": {
                "type": ch.auth.type,
                "header_name": ch.auth.header_name,
                "header_prefix": ch.auth.header_prefix,
                "query_param": ch.auth.query_param,
                "keys": [
                    {"key": k.key, "enabled": k.enabled, "labels": k.labels}
                    for k in keys_data
                ],
            },
            "request": ch.request,
            "response": ch.response,
            "rate_limits": {
                "per_minute": ch.rate_limits.per_minute,
                "per_hour": ch.rate_limits.per_hour,
                "per_day": ch.rate_limits.per_day,
                "per_month": ch.rate_limits.per_month,
            },
            "quota": {
                "per_day": ch.quota.per_day,
                "per_month": ch.quota.per_month,
            },
        }

        CHANNELS_DIR.mkdir(parents=True, exist_ok=True)
        yaml_file = CHANNELS_DIR / f"{name}.yaml"
        with open(yaml_file, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    def get_keys(self, channel_name: str) -> list[APIKeyInfo]:
        raw = self.channel_raw.get(channel_name, {})
        return list(raw.get("keys", []))

    def add_key(self, channel_name: str, key: str, labels: list[str] | None = None) -> APIKeyInfo:
        raw = self.channel_raw.get(channel_name)
        if raw is None:
            raise KeyError(f"渠道 {channel_name} 不存在")
        info = APIKeyInfo(key=key, enabled=True, labels=labels or [])
        raw["keys"].append(info)
        self._save_channel_yaml(channel_name)
        return info

    def remove_key(self, channel_name: str, key: str) -> bool:
        raw = self.channel_raw.get(channel_name)
        if raw is None:
            return False
        before = len(raw["keys"])
        raw["keys"] = [k for k in raw["keys"] if k.key != key]
        removed = len(raw["keys"]) < before
        if removed:
            self._save_channel_yaml(channel_name)
        return removed

    def get_settings(self) -> dict[str, Any]:
        return dict(self.settings)

    def update_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        self.settings.update(updates)
        return self.get_settings()

    def get_channel_priority_list(self) -> list[str]:
        return self.settings.get("channels_priority", list(self.channels.keys()))

    # ---- 监控/报警 (轻量 stub，完整实现由 monitor 模块提供) ----

    def get_channel_status(self, name: str) -> ChannelStatus:
        ch = self.channels.get(name)
        keys_info = self.get_keys(name)
        return ChannelStatus(
            name=name,
            enabled=ch.enabled if ch else False,
            healthy=True,
            keys=[
                KeyStatus(
                    key_masked=k.key[:4] + "****" + k.key[-4:] if len(k.key) > 8 else "****",
                    enabled=k.enabled,
                )
                for k in keys_info
            ],
        )

    def get_all_channel_status(self) -> list[ChannelStatus]:
        return [self.get_channel_status(name) for name in self.channels]

    def get_health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "channels": len(self.channels),
            "enabled": sum(1 for c in self.channels.values() if c.enabled),
        }

# ---- 全局单例 ----
_manager: ConfigManager | None = None


def load_config() -> ConfigManager:
    global _manager
    if _manager is None:
        _manager = ConfigManager()
        _manager.load()
    return _manager


def get_config_manager() -> ConfigManager:
    global _manager
    if _manager is None:
        return load_config()
    return _manager
