"""Shared signal-engine cache: Redis when available, in-process fallback otherwise."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from app.core.logging import get_logger
from app.core.redis_client import get_redis, invalidate_redis
from app.domains.signal_engine_constants import (
    LOCK_TTL_MS,
    SNAPSHOT_TTL_MS,
    TIER_TTL_MS,
    WATCH_TTL_SECONDS,
    Tier,
)

logger = get_logger(__name__)

# In-process fallback (tests + memory:// Redis)
_metric_cache: dict[str, dict[str, tuple[float, Any]]] = {}
_session_store: dict[str, dict[str, Any]] = {}
_snapshots: dict[str, tuple[float, dict[str, Any]]] = {}
_watchers: dict[str, float] = {}
_local_compute_locks: dict[str, asyncio.Lock] = {}
_redis_lock_tokens: dict[str, str] = {}


def _now_ms() -> float:
    return time.monotonic() * 1000


def _metric_key(tenant_id: str, metric_id: str) -> str:
    return f"atlas:signals:{tenant_id}:m:{metric_id}"


def _snapshot_key(tenant_id: str) -> str:
    return f"atlas:signals:{tenant_id}:snapshot"


def _session_key(tenant_id: str) -> str:
    return f"atlas:signals:{tenant_id}:sess"


def _watch_key(tenant_id: str) -> str:
    return f"atlas:signals:watch:{tenant_id}"


def _lock_key(tenant_id: str) -> str:
    return f"atlas:signals:lock:{tenant_id}"


def reset_signal_cache_for_tests() -> None:
    """Clear in-process fallback state between tests."""
    _metric_cache.clear()
    _session_store.clear()
    _snapshots.clear()
    _watchers.clear()
    _local_compute_locks.clear()
    _redis_lock_tokens.clear()


async def get_metric(tenant_id: str, metric_id: str) -> Any | None:
    client = await get_redis()
    if client is not None:
        try:
            raw = await client.get(_metric_key(tenant_id, metric_id))
            if raw is None:
                return None
            return json.loads(raw)
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_metric_get_failed", tenant_id=tenant_id, metric_id=metric_id)

    row = _metric_cache.get(tenant_id, {}).get(metric_id)
    if not row:
        return None
    expires_at, value = row
    if _now_ms() >= expires_at:
        return None
    return value


async def set_metric(tenant_id: str, metric_id: str, tier: Tier, value: Any) -> None:
    ttl_ms = TIER_TTL_MS[tier]
    client = await get_redis()
    if client is not None:
        try:
            await client.set(
                _metric_key(tenant_id, metric_id),
                json.dumps(value, separators=(",", ":")),
                px=ttl_ms,
            )
            return
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_metric_set_failed", tenant_id=tenant_id, metric_id=metric_id)

    bucket = _metric_cache.setdefault(tenant_id, {})
    bucket[metric_id] = (_now_ms() + ttl_ms, value)


async def get_session_value(tenant_id: str, field: str) -> Any | None:
    client = await get_redis()
    if client is not None:
        try:
            raw = await client.hget(_session_key(tenant_id), field)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_session_get_failed", tenant_id=tenant_id, field=field)

    return _session_store.get(tenant_id, {}).get(field)


async def set_session_value(tenant_id: str, field: str, value: Any) -> None:
    client = await get_redis()
    if client is not None:
        try:
            await client.hset(
                _session_key(tenant_id),
                field,
                json.dumps(value, separators=(",", ":")),
            )
            return
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_session_set_failed", tenant_id=tenant_id, field=field)

    bucket = _session_store.setdefault(tenant_id, {})
    bucket[field] = value


async def get_snapshot(tenant_id: str) -> dict[str, Any] | None:
    client = await get_redis()
    if client is not None:
        try:
            raw = await client.get(_snapshot_key(tenant_id))
            if raw is None:
                return None
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else None
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_snapshot_get_failed", tenant_id=tenant_id)

    row = _snapshots.get(tenant_id)
    if row is None:
        return None
    expires_at, payload = row
    if _now_ms() >= expires_at:
        return None
    return payload


async def set_snapshot(tenant_id: str, payload: dict[str, Any]) -> None:
    client = await get_redis()
    if client is not None:
        try:
            await client.set(
                _snapshot_key(tenant_id),
                json.dumps(payload, separators=(",", ":")),
                px=SNAPSHOT_TTL_MS,
            )
            return
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_snapshot_set_failed", tenant_id=tenant_id)

    _snapshots[tenant_id] = (_now_ms() + SNAPSHOT_TTL_MS, payload)


async def clear_watcher(tenant_id: str) -> None:
    client = await get_redis()
    if client is not None:
        try:
            await client.delete(_watch_key(tenant_id))
            return
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_watch_clear_failed", tenant_id=tenant_id)
    _watchers.pop(tenant_id, None)


async def touch_watcher(tenant_id: str) -> None:
    client = await get_redis()
    if client is not None:
        try:
            await client.set(_watch_key(tenant_id), "1", ex=WATCH_TTL_SECONDS)
            return
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_watch_touch_failed", tenant_id=tenant_id)

    _watchers[tenant_id] = _now_ms() + (WATCH_TTL_SECONDS * 1000)


async def list_watched_tenant_ids() -> list[str]:
    client = await get_redis()
    if client is not None:
        try:
            cursor = 0
            tenants: list[str] = []
            pattern = "atlas:signals:watch:*"
            while True:
                cursor, keys = await client.scan(cursor=cursor, match=pattern, count=64)
                for key in keys:
                    prefix = "atlas:signals:watch:"
                    if key.startswith(prefix):
                        tenants.append(key[len(prefix) :])
                if cursor == 0:
                    break
            return tenants
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_watch_list_failed")

    now = _now_ms()
    active = [tenant_id for tenant_id, expires in _watchers.items() if expires > now]
    for tenant_id in list(_watchers):
        if _watchers[tenant_id] <= now:
            _watchers.pop(tenant_id, None)
    return active


async def try_compute_lock(tenant_id: str) -> bool:
    token = uuid.uuid4().hex
    client = await get_redis()
    if client is not None:
        try:
            acquired = await client.set(_lock_key(tenant_id), token, nx=True, px=LOCK_TTL_MS)
            if acquired:
                _redis_lock_tokens[tenant_id] = token
                return True
            return False
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_lock_failed", tenant_id=tenant_id)

    lock = _local_compute_locks.setdefault(tenant_id, asyncio.Lock())
    if lock.locked():
        return False
    await lock.acquire()
    return True


async def release_compute_lock(tenant_id: str) -> None:
    token = _redis_lock_tokens.pop(tenant_id, None)
    client = await get_redis()
    if client is not None and token is not None:
        try:
            script = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""
            await client.eval(script, 1, _lock_key(tenant_id), token)
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_unlock_failed", tenant_id=tenant_id)

    lock = _local_compute_locks.get(tenant_id)
    if isinstance(lock, asyncio.Lock) and lock.locked():
        lock.release()


async def invalidate_tenant(tenant_id: str) -> None:
    client = await get_redis()
    if client is not None:
        try:
            cursor = 0
            keys_to_delete = [_snapshot_key(tenant_id), _session_key(tenant_id)]
            pattern = f"atlas:signals:{tenant_id}:m:*"
            while True:
                cursor, keys = await client.scan(cursor=cursor, match=pattern, count=64)
                keys_to_delete.extend(keys)
                if cursor == 0:
                    break
            if keys_to_delete:
                await client.delete(*keys_to_delete)
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_invalidate_failed", tenant_id=tenant_id)

    _metric_cache.pop(tenant_id, None)
    _session_store.pop(tenant_id, None)
    _snapshots.pop(tenant_id, None)
