"""Key 管理器 - 管理每个渠道的多个 API Key，支持负载均衡和故障切换"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field


from core.models import APIKeyInfo, KeyStatus

logger = logging.getLogger(__name__)

# 连续失败多少次后标记不健康
_FAILURE_THRESHOLD = 3


@dataclass
class _KeyState:
    """单个 Key 的运行时状态"""
    info: APIKeyInfo
    failure_count: int = 0
    last_error: str | None = None
    last_used: float = 0.0
    total_calls: int = 0
    is_healthy: bool = True
    marked_unhealthy_at: float = 0.0


class KeyManager:
    """为每个渠道管理多个 API Key，提供 round-robin 负载均衡和故障切换"""

    def __init__(self) -> None:
        # {channel_name: [_KeyState, ...]}
        self._keys: dict[str, list[_KeyState]] = {}
        # {channel_name: current_round_robin_index}
        self._rr_index: dict[str, int] = {}

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------

    def init_channel(self, channel: str, api_keys: list[APIKeyInfo]) -> None:
        """从配置初始化某个渠道的 key 列表"""
        self._keys[channel] = [_KeyState(info=k) for k in api_keys]
        self._rr_index.setdefault(channel, 0)

    def init_all(self, channel_keys: dict[str, list[APIKeyInfo]]) -> None:
        """批量初始化所有渠道的 key"""
        for channel, keys in channel_keys.items():
            self.init_channel(channel, keys)

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def get_key(self, channel: str) -> APIKeyInfo | None:
        """Round-robin 获取下一个可用 key，无可用 key 返回 None"""
        states = self._keys.get(channel, [])
        healthy_states = [
            s for s in states if s.info.enabled and s.is_healthy
        ]
        if not healthy_states:
            return None

        idx = self._rr_index.get(channel, 0) % len(healthy_states)
        state = healthy_states[idx]
        state.last_used = time.time()
        state.total_calls += 1
        self._rr_index[channel] = (idx + 1) % len(healthy_states)
        return state.info

    def report_failure(self, channel: str, key: str, error: str) -> None:
        """报告某个 key 的调用失败"""
        state = self._find_state(channel, key)
        if state is None:
            return
        state.failure_count += 1
        state.last_error = error
        if state.failure_count >= _FAILURE_THRESHOLD:
            state.is_healthy = False
            state.marked_unhealthy_at = time.time()
            logger.warning(
                "Key %s (渠道 %s) 已标记为不健康，将在后续调用中跳过",
                self._mask_key(key), channel,
            )

    def report_success(self, channel: str, key: str) -> None:
        """报告某个 key 调用成功，重置失败计数"""
        state = self._find_state(channel, key)
        if state is None:
            return
        state.failure_count = 0
        state.last_error = None
        if not state.is_healthy:
            state.is_healthy = True
            logger.info("Key %s (渠道 %s) 已恢复为健康状态", self._mask_key(key), channel)

    def add_key(self, channel: str, key: str, labels: list[str] | None = None) -> None:
        """动态添加一个新的 API Key"""
        api_key = APIKeyInfo(key=key, labels=labels or [])
        state = _KeyState(info=api_key)
        if channel not in self._keys:
            self._keys[channel] = []
            self._rr_index[channel] = 0
        self._keys[channel].append(state)
        logger.info("已为渠道 %s 添加 key: %s", channel, self._mask_key(key))

    def remove_key(self, channel: str, key: str) -> bool:
        """移除指定 key，返回是否成功"""
        states = self._keys.get(channel, [])
        for i, state in enumerate(states):
            if state.info.key == key:
                states.pop(i)
                # 调整 round-robin 索引
                idx = self._rr_index.get(channel, 0)
                if idx >= len(states) and states:
                    self._rr_index[channel] = idx % len(states)
                logger.info("已从渠道 %s 移除 key: %s", channel, self._mask_key(key))
                return True
        return False

    def get_all_keys(self, channel: str) -> list[KeyStatus]:
        """获取指定渠道所有 key 的运行时状态"""
        states = self._keys.get(channel, [])
        result: list[KeyStatus] = []
        for s in states:
            ks = KeyStatus(
                key_masked=self._mask_key(s.info.key),
                enabled=s.info.enabled,
                failure_count=s.failure_count,
                last_error=s.last_error,
                last_used=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s.last_used)) if s.last_used else None,
                total_calls=s.total_calls,
                is_healthy=s.is_healthy,
            )
            result.append(ks)
        return result

    def has_healthy_key(self, channel: str) -> bool:
        """检查该渠道是否有至少一个可用 key"""
        states = self._keys.get(channel, [])
        return any(s.info.enabled and s.is_healthy for s in states)

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _find_state(self, channel: str, key: str) -> _KeyState | None:
        """查找指定 key 的运行时状态"""
        for state in self._keys.get(channel, []):
            if state.info.key == key:
                return state
        return None

    @staticmethod
    def _mask_key(key: str) -> str:
        """将 API Key 脱敏，只显示前4位和后4位"""
        if len(key) <= 8:
            return key[:2] + "*" * (len(key) - 2)
        return key[:4] + "*" * (len(key) - 8) + key[-4:]


# 模块级单例
key_manager = KeyManager()
