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

# Chain snapshots can exceed the short Signal lock TTL; Options Lab does not
# heartbeat yet, so keep a longer floor until a dedicated OL heartbeat lands.
OPTIONS_LAB_LOCK_TTL_MS = max(LOCK_TTL_MS, 60_000)

_snapshots: dict[str, tuple[float, dict[str, Any]]] = {}
_watchers: dict[str, tuple[float, dict[str, Any]]] = {}
_local_compute_locks: dict[str, asyncio.Lock] = {}
_redis_lock_tokens: dict[str, str] = {}
_fingerprints: dict[str, tuple[float, str]] = {}


def _now_ms() -> float:
    return time.monotonic() * 1000


def _snapshot_key(tenant_id: str, *, wings: int, fingerprint: str) -> str:
    return f"atlas:options-lab:{tenant_id}:snapshot:{wings}:{fingerprint}"


# Instrument dimension (Phase 0 / E1-E2). The snapshot key already separates
# instruments through the config fingerprint; the *pointer* and *watch* keys did
# not, so two windows on different underlyings clobbered each other. ``-`` means
# "whatever the tenant desk config says" — the pre-E1 behaviour.
DESK_DEFAULT_INSTRUMENT = "-"


def instrument_slug(underlying: str | None) -> str:
    """Cache-key segment for one instrument (``-`` = tenant desk default)."""
    return (underlying or "").strip().upper() or DESK_DEFAULT_INSTRUMENT


def _watch_key(tenant_id: str, underlying: str | None = None) -> str:
    # ``|`` separates tenant from instrument: tenant ids are UUIDs and trading
    # symbols contain ':' and ' ' but never '|', so the split back is safe.
    return f"atlas:options-lab:watch:{tenant_id}|{instrument_slug(underlying)}"


def _lock_key(tenant_id: str, *, wings: int, fingerprint: str) -> str:
    return f"atlas:options-lab:lock:{tenant_id}:{wings}:{fingerprint}"


def _snap_bucket_key(tenant_id: str, *, wings: int, fingerprint: str) -> str:
    return f"{tenant_id}|{wings}|{fingerprint}"


def _fingerprint_key(
    tenant_id: str, *, wings: int, underlying: str | None = None
) -> str:
    return (
        f"atlas:options-lab:{tenant_id}:fp:{wings}:{instrument_slug(underlying)}"
    )


def reset_options_lab_cache_for_tests() -> None:
    """Clear in-process fallback state between tests."""
    _snapshots.clear()
    _watchers.clear()
    _local_compute_locks.clear()
    _redis_lock_tokens.clear()
    _fingerprints.clear()


async def remember_fingerprint(
    tenant_id: str, *, wings: int, fingerprint: str, underlying: str | None = None
) -> None:
    """Remember the last snapshot fingerprint so SSE can skip Postgres."""
    key = _fingerprint_key(tenant_id, wings=wings, underlying=underlying)
    client = await get_redis()
    if client is not None:
        try:
            await client.set(key, fingerprint, px=SNAPSHOT_TTL_MS)
            return
        except Exception:
            await invalidate_redis()
            logger.warning("options_lab_cache_fp_set_failed", tenant_id=tenant_id)
    _fingerprints[key] = (_now_ms() + SNAPSHOT_TTL_MS, fingerprint)


async def get_fingerprint(
    tenant_id: str, *, wings: int, underlying: str | None = None
) -> str | None:
    key = _fingerprint_key(tenant_id, wings=wings, underlying=underlying)
    client = await get_redis()
    if client is not None:
        try:
            raw = await client.get(key)
            return str(raw) if raw else None
        except Exception:
            await invalidate_redis()
            logger.warning("options_lab_cache_fp_get_failed", tenant_id=tenant_id)
    row = _fingerprints.get(key)
    if row is None:
        return None
    expires_at, fingerprint = row
    if _now_ms() >= expires_at:
        _fingerprints.pop(key, None)
        return None
    return fingerprint


async def clear_fingerprints(tenant_id: str) -> None:
    """Drop remembered fingerprints after Lab config changes."""
    client = await get_redis()
    if client is not None:
        try:
            cursor = 0
            pattern = f"atlas:options-lab:{tenant_id}:fp:*"
            keys: list[str] = []
            while True:
                cursor, batch = await client.scan(cursor=cursor, match=pattern, count=64)
                keys.extend(batch)
                if cursor == 0:
                    break
            if keys:
                await client.delete(*keys)
        except Exception:
            await invalidate_redis()
            logger.warning("options_lab_cache_fp_clear_failed", tenant_id=tenant_id)
    prefix = f"atlas:options-lab:{tenant_id}:fp:"
    for key in list(_fingerprints):
        if key.startswith(prefix):
            _fingerprints.pop(key, None)


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
    underlying: str | None = None,
) -> None:
    client = await get_redis()
    if client is not None:
        try:
            await client.set(
                _snapshot_key(tenant_id, wings=wings, fingerprint=fingerprint),
                json.dumps(payload, separators=(",", ":")),
                px=SNAPSHOT_TTL_MS,
            )
            await remember_fingerprint(
                tenant_id, wings=wings, fingerprint=fingerprint, underlying=underlying
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
    await remember_fingerprint(
        tenant_id, wings=wings, fingerprint=fingerprint, underlying=underlying
    )


async def touch_watcher(
    tenant_id: str, *, wings: int, underlying: str | None = None
) -> None:
    slug = instrument_slug(underlying)
    meta = {"wings": int(wings), "underlying": slug}
    key = _watch_key(tenant_id, underlying)
    client = await get_redis()
    if client is not None:
        try:
            await client.set(
                key,
                json.dumps(meta, separators=(",", ":")),
                ex=WATCH_TTL_SECONDS,
            )
            return
        except Exception:
            await invalidate_redis()
            logger.warning("options_lab_cache_watch_touch_failed", tenant_id=tenant_id)

    _watchers[f"{tenant_id}|{slug}"] = (_now_ms() + (WATCH_TTL_SECONDS * 1000), meta)


async def clear_watcher(tenant_id: str, underlying: str | None = None) -> None:
    key = _watch_key(tenant_id, underlying)
    client = await get_redis()
    if client is not None:
        try:
            await client.delete(key)
            return
        except Exception:
            await invalidate_redis()
            logger.warning("options_lab_cache_watch_clear_failed", tenant_id=tenant_id)
    _watchers.pop(f"{tenant_id}|{instrument_slug(underlying)}", None)


def _split_watch_id(raw: str) -> tuple[str, str]:
    """``{tenant}|{instrument}`` → parts. Legacy keys carry no instrument."""
    tenant_id, sep, slug = raw.partition("|")
    return tenant_id, (slug if sep else DESK_DEFAULT_INSTRUMENT)


def _wings_from_meta(meta: Any) -> int:
    if isinstance(meta, dict) and meta.get("wings") is not None:
        try:
            return int(meta["wings"])
        except (TypeError, ValueError):
            return 15
    return 15


async def list_watched() -> list[tuple[str, int, str]]:
    """Return (tenant_id, wings, instrument) for active Options Lab SSE desks.

    ``instrument`` is ``-`` when the window did not pin one, meaning the worker
    should warm whatever the tenant desk config points at.
    """
    client = await get_redis()
    if client is not None:
        try:
            cursor = 0
            out: list[tuple[str, int, str]] = []
            pattern = "atlas:options-lab:watch:*"
            prefix = "atlas:options-lab:watch:"
            while True:
                cursor, keys = await client.scan(cursor=cursor, match=pattern, count=64)
                for key in keys:
                    if not key.startswith(prefix):
                        continue
                    tenant_id, slug = _split_watch_id(key[len(prefix) :])
                    raw = await client.get(key)
                    meta: Any = None
                    if raw:
                        try:
                            meta = json.loads(raw)
                        except (TypeError, ValueError, json.JSONDecodeError):
                            meta = None
                    out.append((tenant_id, _wings_from_meta(meta), slug))
                if cursor == 0:
                    break
            return out
        except Exception:
            await invalidate_redis()
            logger.warning("options_lab_cache_watch_list_failed")

    now = _now_ms()
    active: list[tuple[str, int, str]] = []
    for watch_id, (expires, meta) in list(_watchers.items()):
        if expires <= now:
            _watchers.pop(watch_id, None)
            continue
        tenant_id, slug = _split_watch_id(watch_id)
        active.append((tenant_id, _wings_from_meta(meta), slug))
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
                px=OPTIONS_LAB_LOCK_TTL_MS,
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
