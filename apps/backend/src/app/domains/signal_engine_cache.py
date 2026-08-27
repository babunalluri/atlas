"""Shared signal-engine cache: Redis when available, in-process fallback otherwise."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Any, Iterable

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
_globals: dict[str, tuple[float, dict[str, Any]]] = {}
_rows: dict[str, tuple[float, dict[str, Any]]] = {}
_watchers: dict[str, float] = {}
_watch_instruments: dict[str, dict[str, float]] = {}
_local_compute_locks: dict[str, asyncio.Lock] = {}
_redis_lock_tokens: dict[str, str] = {}
_redis_session_prune_due_ms: dict[str, float] = {}
SESSION_KEY_TTL_SECONDS = 14 * 24 * 60 * 60
REDIS_SESSION_PRUNE_INTERVAL_MS = 60 * 60 * 1000
_DATED_FIELD_RE = re.compile(r"^(?P<prefix>.+:)(?P<date>\d{4}-\d{2}-\d{2})$")


def _stale_dated_session_fields(
    fields: Iterable[str],
    *,
    max_age_days: int = 14,
) -> list[str]:
    now = time.time()
    max_age = max_age_days * 24 * 60 * 60
    stale: list[str] = []
    for field in fields:
        match = _DATED_FIELD_RE.match(field)
        if not match:
            continue
        try:
            ts = time.mktime(time.strptime(match.group("date"), "%Y-%m-%d"))
        except ValueError:
            continue
        if now - ts > max_age:
            stale.append(field)
    return stale


def _prune_local_session_bucket(bucket: dict[str, Any], *, max_age_days: int = 14) -> None:
    stale = _stale_dated_session_fields(list(bucket), max_age_days=max_age_days)
    for field in stale:
        bucket.pop(field, None)


def _should_prune_redis_session(tenant_id: str) -> bool:
    now = _now_ms()
    due = _redis_session_prune_due_ms.get(tenant_id, 0.0)
    if now < due:
        return False
    _redis_session_prune_due_ms[tenant_id] = now + REDIS_SESSION_PRUNE_INTERVAL_MS
    return True


async def _prune_redis_session_hash(client: Any, tenant_id: str) -> None:
    key = _session_key(tenant_id)
    fields = await client.hkeys(key)
    stale = _stale_dated_session_fields([str(field) for field in fields])
    if stale:
        await client.hdel(key, *stale)


def _now_ms() -> float:
    return time.monotonic() * 1000


def _metric_key(tenant_id: str, metric_id: str) -> str:
    return f"atlas:signals:{tenant_id}:m:{metric_id}"


def _snapshot_key(tenant_id: str) -> str:
    return f"atlas:signals:{tenant_id}:snapshot"


def _globals_key(tenant_id: str) -> str:
    return f"atlas:signals:{tenant_id}:globals"


def _row_key(tenant_id: str, instrument: str) -> str:
    from app.domains.signal_matrix import instrument_key

    return f"atlas:signals:{tenant_id}:row:{instrument_key(instrument)}"


def _session_key(tenant_id: str) -> str:
    return f"atlas:signals:{tenant_id}:sess"


def _epoch_key(tenant_id: str) -> str:
    # Outside m:* so scoped / Stop flushes never TTL-reset the generation.
    return f"atlas:signals:{tenant_id}:config_epoch"


def _watch_key(tenant_id: str) -> str:
    return f"atlas:signals:watch:{tenant_id}"


def _watch_instruments_key(tenant_id: str) -> str:
    return f"atlas:signals:watchinstr:{tenant_id}"


def _lock_key(tenant_id: str) -> str:
    return f"atlas:signals:lock:{tenant_id}"


_epoch_local: dict[str, int] = {}


def reset_signal_cache_for_tests() -> None:
    """Clear in-process fallback state between tests."""
    _metric_cache.clear()
    _session_store.clear()
    _snapshots.clear()
    _globals.clear()
    _rows.clear()
    _watchers.clear()
    _watch_instruments.clear()
    _local_compute_locks.clear()
    _redis_lock_tokens.clear()
    _redis_session_prune_due_ms.clear()
    _epoch_local.clear()


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


async def set_metric(
    tenant_id: str,
    metric_id: str,
    tier: Tier,
    value: Any,
    *,
    ttl_ms: int | None = None,
) -> None:
    ttl = int(ttl_ms) if ttl_ms is not None else TIER_TTL_MS[tier]
    if ttl <= 0:
        ttl = TIER_TTL_MS[tier]
    client = await get_redis()
    if client is not None:
        try:
            await client.set(
                _metric_key(tenant_id, metric_id),
                json.dumps(value, separators=(",", ":")),
                px=ttl,
            )
            return
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_metric_set_failed", tenant_id=tenant_id, metric_id=metric_id)

    bucket = _metric_cache.setdefault(tenant_id, {})
    bucket[metric_id] = (_now_ms() + ttl, value)


async def delete_metric(tenant_id: str, metric_id: str) -> None:
    """Drop one metric key (e.g. setup memo after auto-ATM persist)."""
    client = await get_redis()
    if client is not None:
        try:
            await client.delete(_metric_key(tenant_id, metric_id))
        except Exception:
            await invalidate_redis()
            logger.warning(
                "signal_cache_metric_delete_failed",
                tenant_id=tenant_id,
                metric_id=metric_id,
            )
    bucket = _metric_cache.get(tenant_id)
    if bucket is not None:
        bucket.pop(metric_id, None)


async def get_session_value(tenant_id: str, field: str) -> Any | None:
    client = await get_redis()
    if client is not None:
        try:
            if _should_prune_redis_session(tenant_id):
                await _prune_redis_session_hash(client, tenant_id)
            raw = await client.hget(_session_key(tenant_id), field)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_session_get_failed", tenant_id=tenant_id, field=field)

    bucket = _session_store.get(tenant_id, {})
    if bucket:
        _prune_local_session_bucket(bucket)
    return bucket.get(field)


async def set_session_value(tenant_id: str, field: str, value: Any) -> None:
    client = await get_redis()
    if client is not None:
        try:
            key = _session_key(tenant_id)
            if _should_prune_redis_session(tenant_id):
                await _prune_redis_session_hash(client, tenant_id)
            await client.hset(
                key,
                field,
                json.dumps(value, separators=(",", ":")),
            )
            # Set TTL only when the hash has none; avoid resetting on every hot write.
            await client.expire(key, SESSION_KEY_TTL_SECONDS, nx=True)
            return
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_session_set_failed", tenant_id=tenant_id, field=field)

    bucket = _session_store.setdefault(tenant_id, {})
    _prune_local_session_bucket(bucket)
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


async def set_snapshot(
    tenant_id: str,
    payload: dict[str, Any],
    *,
    ttl_ms: int | None = None,
    force: bool = False,
) -> bool:
    """Persist a tenant snapshot. Returns False when the write was refused.

    Provisional ``feed_source=starting`` frames must not clobber an existing live
    board (timeout / race). Pass ``force=True`` for Start/Stop and intentional resets.

    When ``payload["config_epoch"]`` is older than the tenant's current epoch, the
    write is refused so a pre-switch tick cannot resurrect the previous board.
    """
    if (
        not force
        and isinstance(payload, dict)
        and payload.get("feed_source") == "starting"
    ):
        existing = await get_snapshot(tenant_id)
        if (
            isinstance(existing, dict)
            and existing.get("feed_source") == "live"
            and (existing.get("metrics") or existing.get("evaluable"))
        ):
            return False

    if not force and isinstance(payload, dict) and payload.get("config_epoch") is not None:
        try:
            stamped = int(payload["config_epoch"])
        except (TypeError, ValueError):
            stamped = -1
        current = await get_config_epoch(tenant_id)
        if current > 0 and stamped >= 0 and stamped < current:
            logger.info(
                "signal_snapshot_stale_epoch_refused",
                tenant_id=tenant_id,
                stamped=stamped,
                current=current,
            )
            return False

    ttl = int(ttl_ms) if ttl_ms is not None else SNAPSHOT_TTL_MS
    if ttl <= 0:
        ttl = SNAPSHOT_TTL_MS
    client = await get_redis()
    if client is not None:
        try:
            await client.set(
                _snapshot_key(tenant_id),
                json.dumps(payload, separators=(",", ":")),
                px=ttl,
            )
            # Dual-write matrix halves so warm instrument switches skip rebuild.
            await _dual_write_matrix(client, tenant_id, payload, ttl_ms=ttl)
            return True
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_snapshot_set_failed", tenant_id=tenant_id)

    _snapshots[tenant_id] = (_now_ms() + ttl, payload)
    await _dual_write_matrix_local(tenant_id, payload, ttl_ms=ttl)
    return True


async def _dual_write_matrix(
    client: Any,
    tenant_id: str,
    payload: dict[str, Any],
    *,
    ttl_ms: int,
) -> None:
    from app.domains.signal_matrix import instrument_key, split_snapshot

    globals_doc, row_doc = split_snapshot(payload)
    await client.set(
        _globals_key(tenant_id),
        json.dumps(globals_doc, separators=(",", ":")),
        px=ttl_ms,
    )
    instrument = str(row_doc.get("instrument") or "").strip()
    if instrument:
        await client.set(
            _row_key(tenant_id, instrument),
            json.dumps(row_doc, separators=(",", ":")),
            px=ttl_ms,
        )
        # Keep a pointer so legacy readers know the primary row key.
        await client.set(
            f"atlas:signals:{tenant_id}:primary_row",
            instrument_key(instrument),
            px=ttl_ms,
        )


async def _dual_write_matrix_local(
    tenant_id: str,
    payload: dict[str, Any],
    *,
    ttl_ms: int,
) -> None:
    from app.domains.signal_matrix import instrument_key, split_snapshot

    globals_doc, row_doc = split_snapshot(payload)
    expires = _now_ms() + ttl_ms
    _globals[tenant_id] = (expires, globals_doc)
    instrument = str(row_doc.get("instrument") or "").strip()
    if instrument:
        _rows[f"{tenant_id}:{instrument_key(instrument)}"] = (expires, row_doc)


async def get_globals(tenant_id: str) -> dict[str, Any] | None:
    client = await get_redis()
    if client is not None:
        try:
            raw = await client.get(_globals_key(tenant_id))
            if raw is None:
                return None
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else None
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_globals_get_failed", tenant_id=tenant_id)

    row = _globals.get(tenant_id)
    if row is None:
        return None
    expires_at, payload = row
    if _now_ms() >= expires_at:
        return None
    return payload


async def set_globals(
    tenant_id: str,
    payload: dict[str, Any],
    *,
    ttl_ms: int | None = None,
) -> None:
    ttl = int(ttl_ms) if ttl_ms is not None else SNAPSHOT_TTL_MS
    if ttl <= 0:
        ttl = SNAPSHOT_TTL_MS
    client = await get_redis()
    if client is not None:
        try:
            await client.set(
                _globals_key(tenant_id),
                json.dumps(payload, separators=(",", ":")),
                px=ttl,
            )
            return
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_globals_set_failed", tenant_id=tenant_id)
    _globals[tenant_id] = (_now_ms() + ttl, payload)


async def get_row(tenant_id: str, instrument: str) -> dict[str, Any] | None:
    if not (instrument or "").strip():
        return None
    from app.domains.signal_matrix import instrument_key

    key = instrument_key(instrument)
    client = await get_redis()
    if client is not None:
        try:
            raw = await client.get(_row_key(tenant_id, instrument))
            if raw is None:
                return None
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else None
        except Exception:
            await invalidate_redis()
            logger.warning(
                "signal_cache_row_get_failed",
                tenant_id=tenant_id,
                instrument=key,
            )

    row = _rows.get(f"{tenant_id}:{key}")
    if row is None:
        return None
    expires_at, payload = row
    if _now_ms() >= expires_at:
        return None
    return payload


async def set_row(
    tenant_id: str,
    instrument: str,
    payload: dict[str, Any],
    *,
    ttl_ms: int | None = None,
) -> None:
    if not (instrument or "").strip():
        return
    from app.domains.signal_matrix import instrument_key

    key = instrument_key(instrument)
    ttl = int(ttl_ms) if ttl_ms is not None else SNAPSHOT_TTL_MS
    if ttl <= 0:
        ttl = SNAPSHOT_TTL_MS
    stamped = {**payload, "instrument": instrument}
    client = await get_redis()
    if client is not None:
        try:
            await client.set(
                _row_key(tenant_id, instrument),
                json.dumps(stamped, separators=(",", ":")),
                px=ttl,
            )
            return
        except Exception:
            await invalidate_redis()
            logger.warning(
                "signal_cache_row_set_failed",
                tenant_id=tenant_id,
                instrument=key,
            )
    _rows[f"{tenant_id}:{key}"] = (_now_ms() + ttl, stamped)


async def merged_frame(
    tenant_id: str,
    *,
    instrument: str | None = None,
) -> dict[str, Any] | None:
    """Prefer globals+row merge; fall back to legacy monolithic snapshot.

    Incomplete matrix halves (globals without a row, or vice versa with no
    usable board fields) must not shadow the full ``snapshot`` key — that
    caused SSE frames to lose ``passed`` / entry after dual-write of a
    globals-only document.
    """
    from app.domains.signal_matrix import merge_globals_row

    globals_doc = await get_globals(tenant_id)
    selected = (instrument or "").strip()
    if not selected:
        # Prefer primary pointer, else underlying on legacy snapshot.
        client = await get_redis()
        if client is not None:
            try:
                raw = await client.get(f"atlas:signals:{tenant_id}:primary_row")
                if raw:
                    selected = str(raw)
            except Exception:
                pass
        if not selected:
            snap = await get_snapshot(tenant_id)
            if isinstance(snap, dict):
                under = snap.get("underlying") if isinstance(snap.get("underlying"), dict) else {}
                selected = str(snap.get("instrument") or under.get("symbol") or "")
    row_doc = await get_row(tenant_id, selected) if selected else None
    # Require a row document before preferring the matrix merge. Globals alone
    # are not a complete desk frame.
    if row_doc is not None:
        merged = merge_globals_row(globals_doc, row_doc)
        if merged is not None:
            return merged
    return await get_snapshot(tenant_id)


async def clear_watcher(tenant_id: str, *, instrument: str | None = None) -> None:
    client = await get_redis()
    if instrument:
        from app.domains.signal_matrix import instrument_key

        key = instrument_key(instrument)
        if client is not None:
            try:
                await client.hdel(_watch_instruments_key(tenant_id), key)
                return
            except Exception:
                await invalidate_redis()
                logger.warning("signal_cache_watch_instr_clear_failed", tenant_id=tenant_id)
        bucket = _watch_instruments.get(tenant_id)
        if bucket is not None:
            bucket.pop(key, None)
        return

    if client is not None:
        try:
            await client.delete(_watch_key(tenant_id))
            await client.delete(_watch_instruments_key(tenant_id))
            return
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_watch_clear_failed", tenant_id=tenant_id)
    _watchers.pop(tenant_id, None)
    _watch_instruments.pop(tenant_id, None)


async def touch_watcher(tenant_id: str, *, instrument: str | None = None) -> None:
    client = await get_redis()
    if client is not None:
        try:
            await client.set(_watch_key(tenant_id), "1", ex=WATCH_TTL_SECONDS)
            if instrument:
                from app.domains.signal_matrix import instrument_key

                key = instrument_key(instrument)
                pipe = client.pipeline()
                pipe.hset(_watch_instruments_key(tenant_id), key, "1")
                pipe.expire(_watch_instruments_key(tenant_id), WATCH_TTL_SECONDS)
                await pipe.execute()
            return
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_watch_touch_failed", tenant_id=tenant_id)

    _watchers[tenant_id] = _now_ms() + (WATCH_TTL_SECONDS * 1000)
    if instrument:
        from app.domains.signal_matrix import instrument_key

        key = instrument_key(instrument)
        bucket = _watch_instruments.setdefault(tenant_id, {})
        bucket[key] = _now_ms() + (WATCH_TTL_SECONDS * 1000)


async def list_watched_instruments(tenant_id: str) -> list[str]:
    """Instrument keys currently watched for this tenant (may be Redis-safe keys)."""
    client = await get_redis()
    if client is not None:
        try:
            fields = await client.hkeys(_watch_instruments_key(tenant_id))
            return [str(f) for f in fields]
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_watch_instr_list_failed", tenant_id=tenant_id)

    now = _now_ms()
    bucket = _watch_instruments.get(tenant_id) or {}
    alive = [k for k, exp in bucket.items() if exp > now]
    for k in list(bucket):
        if bucket[k] <= now:
            bucket.pop(k, None)
    return alive


async def watcher_alive(tenant_id: str) -> bool:
    """O(1) check whether this tenant's Signal SSE desk is open."""
    client = await get_redis()
    if client is not None:
        try:
            return bool(await client.exists(_watch_key(tenant_id)))
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_watch_exists_failed", tenant_id=tenant_id)

    expires = _watchers.get(tenant_id)
    if expires is None:
        return False
    if expires <= _now_ms():
        _watchers.pop(tenant_id, None)
        return False
    return True


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


async def compute_lock_held(tenant_id: str) -> bool:
    """True while a worker/SSE holder is computing a fresh snapshot."""
    client = await get_redis()
    if client is not None:
        try:
            return bool(await client.exists(_lock_key(tenant_id)))
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_lock_exists_failed", tenant_id=tenant_id)

    lock = _local_compute_locks.get(tenant_id)
    return isinstance(lock, asyncio.Lock) and lock.locked()


async def extend_compute_lock(tenant_id: str) -> bool:
    """Refresh Redis lock TTL while a long state() is in flight.

    Local locks do not expire; returns True so heartbeats are no-ops in-memory.
    """
    token = _redis_lock_tokens.get(tenant_id)
    if token is None:
        return tenant_id in _local_compute_locks and _local_compute_locks[tenant_id].locked()

    client = await get_redis()
    if client is None:
        return False
    try:
        script = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""
        extended = await client.eval(
            script, 1, _lock_key(tenant_id), token, str(LOCK_TTL_MS)
        )
        return bool(extended)
    except Exception:
        await invalidate_redis()
        logger.warning("signal_cache_lock_extend_failed", tenant_id=tenant_id)
        return False


def start_compute_lock_heartbeat(tenant_id: str) -> asyncio.Task[None]:
    """Extend the Redis compute lock until cancelled (no-op for in-memory locks)."""
    from app.domains.signal_engine_constants import LOCK_HEARTBEAT_SECONDS

    async def _heartbeat() -> None:
        while True:
            await asyncio.sleep(LOCK_HEARTBEAT_SECONDS)
            if not await extend_compute_lock(tenant_id):
                return

    return asyncio.create_task(
        _heartbeat(), name=f"signal-lock-hb-{tenant_id}"
    )


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


async def delete_session_fields(tenant_id: str, fields: Iterable[str]) -> None:
    """Drop selected hash fields; leaves Options Lab / other session data intact."""
    names = [f for f in fields if f]
    if not names:
        return
    client = await get_redis()
    if client is not None:
        try:
            await client.hdel(_session_key(tenant_id), *names)
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_session_hdel_failed", tenant_id=tenant_id)
    bucket = _session_store.get(tenant_id)
    if bucket is not None:
        for field in names:
            bucket.pop(field, None)


async def list_session_fields(tenant_id: str) -> list[str]:
    client = await get_redis()
    if client is not None:
        try:
            raw = await client.hkeys(_session_key(tenant_id))
            return [str(f) for f in raw]
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_session_hkeys_failed", tenant_id=tenant_id)
    bucket = _session_store.get(tenant_id) or {}
    return list(bucket.keys())


# Flushed on underlying / FUT / ATM config changes. Global slow tiers
# (yahoo_*, india_vix, crude_oil, aux_quotes, nse_slow, dow_jones) stay warm.
UNDERLYING_DEPENDENT_METRICS: tuple[str, ...] = (
    "levels",
    "trend",
    "atm_iv",
    "option_chain",
    "setup",
)

# Session fields keyed by date (legacy) or symbol+date — wipe on underlying switch
# so NIFTY spot is not subtracted from a SENSEX session open.
UNDERLYING_SESSION_FIELD_PREFIXES: tuple[str, ...] = (
    "underlying_open:",
    "straddle_session_open:",
    "iv_day_high:",
    "iv_session_open:",
    "oi_baseline:",
)


def _is_underlying_session_field(field: str) -> bool:
    return any(field.startswith(prefix) for prefix in UNDERLYING_SESSION_FIELD_PREFIXES)


async def get_config_epoch(tenant_id: str) -> int:
    client = await get_redis()
    if client is not None:
        try:
            raw = await client.get(_epoch_key(tenant_id))
            value = int(raw) if raw is not None else 0
            local = int(_epoch_local.get(tenant_id, 0))
            if value < local:
                # Redis lost a bump taken while it was down — stay monotonic.
                return local
            _epoch_local[tenant_id] = value
            return value
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_epoch_get_failed", tenant_id=tenant_id)
    # Redis blip: return last-known, never 0 — a 0 stamp marks the whole tick
    # stale and set_snapshot refuses the finished work once Redis recovers
    # (pilot 05:14:34 "epoch 0 vs 11" drop).
    return int(_epoch_local.get(tenant_id, 0))


async def bump_config_epoch(tenant_id: str) -> int:
    """Atomic monotonic generation — Redis INCR, no TTL (must not reset)."""
    client = await get_redis()
    if client is not None:
        try:
            # INCR creates the key at 1 with no expiry when missing.
            value = int(await client.incr(_epoch_key(tenant_id)))
            local = int(_epoch_local.get(tenant_id, 0))
            if value <= local:
                # Redis lost bumps taken during a blip — heal it forward so a
                # new bump is always greater than anything already stamped.
                value = local + 1
                await client.set(_epoch_key(tenant_id), value)
            _epoch_local[tenant_id] = value
            return value
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_epoch_incr_failed", tenant_id=tenant_id)
    nxt = int(_epoch_local.get(tenant_id, 0)) + 1
    _epoch_local[tenant_id] = nxt
    return nxt


async def list_metric_ids(tenant_id: str) -> list[str]:
    """Return known metric ids for a tenant (local + Redis scan best-effort)."""
    client = await get_redis()
    if client is not None:
        try:
            found: list[str] = []
            cursor = 0
            prefix = f"atlas:signals:{tenant_id}:m:"
            pattern = f"{prefix}*"
            while True:
                cursor, keys = await client.scan(cursor=cursor, match=pattern, count=64)
                for key in keys:
                    raw = key.decode() if isinstance(key, (bytes, bytearray)) else str(key)
                    if raw.startswith(prefix):
                        found.append(raw[len(prefix) :])
                if cursor == 0:
                    break
            return found
        except Exception:
            await invalidate_redis()
            logger.warning("signal_cache_metric_list_failed", tenant_id=tenant_id)
    bucket = _metric_cache.get(tenant_id) or {}
    return list(bucket.keys())


async def invalidate_underlying_dependent(tenant_id: str) -> None:
    """Config-patch / Stop flush: underlying metrics + rows + session opens.

    Keeps yahoo / VIX / crude / aux / NSE / Dow warm — and keeps the shared
    ``globals`` Redis blob so pinned-matrix switches do not cold-rebuild globals.
    """
    from app.domains.signal_matrix import is_row_scoped_metric_id

    for metric_id in UNDERLYING_DEPENDENT_METRICS:
        await delete_metric(tenant_id, metric_id)
    # Instrument-scoped variants (levels:NSE:NIFTY_50, …) must flush too.
    for metric_id in await list_metric_ids(tenant_id):
        if is_row_scoped_metric_id(metric_id):
            await delete_metric(tenant_id, metric_id)
    session_fields = [
        f for f in await list_session_fields(tenant_id) if _is_underlying_session_field(f)
    ]
    if session_fields:
        await delete_session_fields(tenant_id, session_fields)
    client = await get_redis()
    if client is not None:
        try:
            keys = [_snapshot_key(tenant_id)]
            # Drop instrument rows only — leave :globals intact.
            cursor = 0
            pattern = f"atlas:signals:{tenant_id}:row:*"
            while True:
                cursor, found = await client.scan(cursor=cursor, match=pattern, count=64)
                keys.extend(found)
                if cursor == 0:
                    break
            await client.delete(*keys)
            await client.delete(f"atlas:signals:{tenant_id}:primary_row")
        except Exception:
            await invalidate_redis()
            logger.warning(
                "signal_cache_underlying_invalidate_failed", tenant_id=tenant_id
            )
    _snapshots.pop(tenant_id, None)
    prefix = f"{tenant_id}:"
    for key in list(_rows):
        if key.startswith(prefix):
            _rows.pop(key, None)


async def invalidate_tenant(tenant_id: str) -> None:
    """Full tenant wipe (tests / hard reset). Preserves config_epoch key."""
    client = await get_redis()
    if client is not None:
        try:
            cursor = 0
            keys_to_delete = [
                _snapshot_key(tenant_id),
                _session_key(tenant_id),
                _globals_key(tenant_id),
                f"atlas:signals:{tenant_id}:primary_row",
            ]
            patterns = (
                f"atlas:signals:{tenant_id}:m:*",
                f"atlas:signals:{tenant_id}:row:*",
            )
            for pattern in patterns:
                cursor = 0
                while True:
                    cursor, keys = await client.scan(
                        cursor=cursor, match=pattern, count=64
                    )
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
    _globals.pop(tenant_id, None)
    prefix = f"{tenant_id}:"
    for key in list(_rows):
        if key.startswith(prefix):
            _rows.pop(key, None)
