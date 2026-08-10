"""Shared async Redis client for distributed quotas, locks, and run state."""

from __future__ import annotations

import time
from typing import Any

from app.core.logging import get_logger
from app.core.settings import get_settings

logger = get_logger(__name__)

_redis: Any | None = None
_next_retry_at: float = 0.0
_RETRY_COOLDOWN_SECONDS = 5.0

# Atomic INCR; always refresh TTL so sustained load cannot let the key expire
# while slots are still held (which would reset the counter and over-admit).
_ACQUIRE_SLOT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count > tonumber(ARGV[2]) then
  redis.call('DECR', KEYS[1])
  return 0
end
redis.call('EXPIRE', KEYS[1], ARGV[1])
return 1
"""

_LEADER_RENEW_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

_LEADER_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


def redis_enabled() -> bool:
    """True when REDIS_URL points at a real Redis instance."""
    url = (get_settings().redis_url or "").strip()
    if not url or url in {"memory://", "memory", "none", "off"}:
        return False
    return True


async def get_redis() -> Any | None:
    """Return a shared async Redis client, or None when disabled/unavailable.

    Returns the cached client without health-checking. Callers should use
    ``invalidate_redis`` after a command failure so the next call reconnects.
    """
    global _redis, _next_retry_at
    if not redis_enabled():
        return None
    if _redis is not None:
        return _redis
    now = time.monotonic()
    if now < _next_retry_at:
        return None
    try:
        from redis.asyncio import Redis

        client = Redis.from_url(
            get_settings().redis_url.strip(),
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        await client.ping()
        _redis = client
        _next_retry_at = 0.0
        return _redis
    except Exception as exc:
        logger.warning("redis_unavailable", error=str(exc))
        _next_retry_at = time.monotonic() + _RETRY_COOLDOWN_SECONDS
        return None


async def invalidate_redis(*, cooldown: bool = True) -> None:
    """Drop the cached client after a command failure so the next call reconnects."""
    global _redis, _next_retry_at
    if _redis is not None:
        try:
            await _redis.aclose()
        except Exception:
            pass
    _redis = None
    if cooldown:
        _next_retry_at = time.monotonic() + _RETRY_COOLDOWN_SECONDS


async def close_redis() -> None:
    global _redis, _next_retry_at
    if _redis is not None:
        try:
            await _redis.aclose()
        except Exception:
            pass
    _redis = None
    _next_retry_at = 0.0


def reset_redis_state_for_tests() -> None:
    """Clear module singletons between tests."""
    global _redis, _next_retry_at
    _redis = None
    _next_retry_at = 0.0


async def acquire_counter_slot(key: str, *, limit: int, ttl_seconds: int = 600) -> bool:
    """Atomically increment a counter with TTL; False if over limit."""
    client = await get_redis()
    if client is None:
        return False
    try:
        allowed = await client.eval(_ACQUIRE_SLOT_SCRIPT, 1, key, ttl_seconds, limit)
        return int(allowed) == 1
    except Exception:
        await invalidate_redis()
        raise


async def release_counter_slot(key: str) -> None:
    client = await get_redis()
    if client is None:
        return
    try:
        val = await client.decr(key)
        if val < 0:
            await client.set(key, 0, ex=600)
    except Exception:
        await invalidate_redis()
        raise


async def renew_leader_lock(key: str, instance_id: str, ttl_seconds: int) -> bool:
    client = await get_redis()
    if client is None:
        return False
    try:
        return int(await client.eval(_LEADER_RENEW_SCRIPT, 1, key, instance_id, ttl_seconds)) == 1
    except Exception:
        await invalidate_redis()
        raise


async def release_leader_lock(key: str, instance_id: str) -> None:
    client = await get_redis()
    if client is None:
        return
    try:
        await client.eval(_LEADER_RELEASE_SCRIPT, 1, key, instance_id)
    except Exception:
        await invalidate_redis()
        raise
