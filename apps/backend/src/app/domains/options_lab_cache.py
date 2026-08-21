"""Options Lab snapshot cache: Redis when available, in-process fallback otherwise.

Mirrors signal_engine_cache keying/TTL patterns with an options-lab namespace so
chain SSE can coalesce independently of the Signal Engine board.
"""

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
    WATCH_TTL_SECONDS,
)

logger = get_logger(__name__)

_snapshots: dict[str, tuple[float, dict[str, Any]]] = {}
_watchers: dict[str, tuple[float, dict[str, Any]]] = {}
_local_compute_locks: dict[str, asyncio.Lock] = {}
_redis_lock_tokens: dict[str, str] = {}


def _now_ms() -> float:
    return time.monotonic() * 1000


def _snapshot_key(tenant_id: str, *, wings: int, fingerprint: str) -> str:
    return f"atlas:options-lab:{tenant_id}:snapshot:{wings}:{fingerprint}"


def _watch_key(tenant_id: str) -> str:
    return f"atlas:options-lab:watch:{tenant_id}"


def _lock_key(tenant_id: str, *, wings: int, fingerprint: str) -> str:
    return f"atlas:options-lab:lock:{tenant_id}:{wings}:{fingerprint}"


def _snap_bucket_key(tenant_id: str, *, wings: int, fingerprint: str) -> str:
    return f"{tenant_id}|{wings}|{fingerprint}"


def reset_options_lab_cache_for_tests() -> None:
    """Clear in-process fallback state between tests."""
    _snapshots.clear()
    _watchers.clear()
    _local_compute_locks.clear()
    _redis_lock_tokens.clear()


async def get_snapshot(
    tenant_id: str,
    *,
    wings: int,
    fingerprint: str,
) -> dict[str, Any] | None:
    client = await get_redis()
    if client is not None:
        try:
            raw = await client.get(_snapshot_key(tenant_id, wings=wings, fingerprint=fingerprint))
            if raw is None:
                return None
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else None
        except Exception:
            await invalidate_redis()
            logger.warning("options_lab_cache_snapshot_get_failed", tenant_id=tenant_id)

    row = _snapshots.get(_snap_bucket_key(tenant_id, wings=wings, fingerprint=fingerprint))
    if row is None:
        return None
    expires_at, payload = row
    if _now_ms() >= expires_at:
        return None
    return payload


async def set_snapshot(
    tenant_id: str,
    payload: dict[str, Any],
    *,
    wings: int,
    fingerprint: str,
) -> None:
    client = await get_redis()
    if client is not None:
        try:
            await client.set(
                _snapshot_key(tenant_id, wings=wings, fingerprint=fingerprint),
                json.dumps(payload, separators=(",", ":")),
                px=SNAPSHOT_TTL_MS,
            )
            return
        except Exception:
            await invalidate_redis()
            logger.warning("options_lab_cache_snapshot_set_failed", tenant_id=tenant_id)

    bucket = _snap_bucket_key(tenant_id, wings=wings, fingerprint=fingerprint)
    _snapshots[bucket] = (_now_ms() + SNAPSHOT_TTL_MS, payload)
    # Bound in-process growth across wings/fingerprint churn.
    now = _now_ms()
    stale = [key for key, (expires, _payload) in _snapshots.items() if expires <= now]
    for key in stale:
        _snapshots.pop(key, None)
    if len(_snapshots) > 64:
        oldest = sorted(_snapshots.items(), key=lambda item: item[1][0])[
            : max(0, len(_snapshots) - 64)
        ]
        for key, _row in oldest:
            _snapshots.pop(key, None)


async def touch_watcher(tenant_id: str, *, wings: int) -> None:
    meta = {"wings": int(wings)}
    client = await get_redis()
    if client is not None:
        try:
            await client.set(
                _watch_key(tenant_id),
                json.dumps(meta, separators=(",", ":")),
                ex=WATCH_TTL_SECONDS,
            )
            return
        except Exception:
            await invalidate_redis()
            logger.warning("options_lab_cache_watch_touch_failed", tenant_id=tenant_id)

    _watchers[tenant_id] = (_now_ms() + (WATCH_TTL_SECONDS * 1000), meta)


async def clear_watcher(tenant_id: str) -> None:
    client = await get_redis()
    if client is not None:
        try:
            await client.delete(_watch_key(tenant_id))
            return
        except Exception:
            await invalidate_redis()
            logger.warning("options_lab_cache_watch_clear_failed", tenant_id=tenant_id)
    _watchers.pop(tenant_id, None)


async def list_watched() -> list[tuple[str, int]]:
    """Return (tenant_id, wings) for active Options Lab SSE desks."""
    client = await get_redis()
    if client is not None:
        try:
            cursor = 0
            out: list[tuple[str, int]] = []
            pattern = "atlas:options-lab:watch:*"
            prefix = "atlas:options-lab:watch:"
            while True:
                cursor, keys = await client.scan(cursor=cursor, match=pattern, count=64)
                for key in keys:
                    if not key.startswith(prefix):
                        continue
                    tenant_id = key[len(prefix) :]
                    raw = await client.get(key)
                    wings = 15
                    if raw:
                        try:
                            meta = json.loads(raw)
                            if isinstance(meta, dict) and meta.get("wings") is not None:
                                wings = int(meta["wings"])
                        except (TypeError, ValueError, json.JSONDecodeError):
                            pass
                    out.append((tenant_id, wings))
                if cursor == 0:
                    break
            return out
        except Exception:
            await invalidate_redis()
            logger.warning("options_lab_cache_watch_list_failed")

    now = _now_ms()
    active: list[tuple[str, int]] = []
    for tenant_id, (expires, meta) in list(_watchers.items()):
        if expires <= now:
            _watchers.pop(tenant_id, None)
            continue
        wings = 15
        if isinstance(meta, dict) and meta.get("wings") is not None:
            try:
                wings = int(meta["wings"])
            except (TypeError, ValueError):
                wings = 15
        active.append((tenant_id, wings))
    return active


async def try_compute_lock(tenant_id: str, *, wings: int, fingerprint: str) -> bool:
    token = uuid.uuid4().hex
    lock_id = _snap_bucket_key(tenant_id, wings=wings, fingerprint=fingerprint)
    client = await get_redis()
    if client is not None:
        try:
            acquired = await client.set(
                _lock_key(tenant_id, wings=wings, fingerprint=fingerprint),
                token,
                nx=True,
                px=LOCK_TTL_MS,
            )
            if acquired:
                _redis_lock_tokens[lock_id] = token
                return True
            return False
        except Exception:
            await invalidate_redis()
            logger.warning("options_lab_cache_lock_failed", tenant_id=tenant_id)

    lock = _local_compute_locks.setdefault(lock_id, asyncio.Lock())
    if lock.locked():
        return False
    await lock.acquire()
    return True


async def release_compute_lock(tenant_id: str, *, wings: int, fingerprint: str) -> None:
    lock_id = _snap_bucket_key(tenant_id, wings=wings, fingerprint=fingerprint)
    token = _redis_lock_tokens.pop(lock_id, None)
    client = await get_redis()
    if client is not None and token is not None:
        try:
            script = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""
            await client.eval(
                script,
                1,
                _lock_key(tenant_id, wings=wings, fingerprint=fingerprint),
                token,
            )
        except Exception:
            await invalidate_redis()
            logger.warning("options_lab_cache_unlock_failed", tenant_id=tenant_id)

    lock = _local_compute_locks.get(lock_id)
    if isinstance(lock, asyncio.Lock) and lock.locked():
        lock.release()
    # Drop idle local locks so wings/fingerprint churn cannot leak forever.
    idle = _local_compute_locks.get(lock_id)
    if isinstance(idle, asyncio.Lock) and not idle.locked():
        _local_compute_locks.pop(lock_id, None)
