"""Param Chart Redis / in-process month-pack cache."""

from __future__ import annotations

import json
import time
from typing import Any

from app.core.logging import get_logger
from app.core.redis_client import get_redis, invalidate_redis
from app.domains.signal_engine_constants import (
    SNAPSHOT_TTL_MS,
    TIER_TTL_MS,
    WATCH_TTL_SECONDS,
)

logger = get_logger(__name__)

# Month packs are cold; keep longer than Signal snapshots.
MONTH_PACK_TTL_MS = max(TIER_TTL_MS["slow"], 3_600_000)
# Failed / empty kite backfills — short TTL so UI can recover quickly.
STALE_PACK_TTL_MS = 60_000

_packs: dict[str, tuple[float, dict[str, Any]]] = {}
_watchers: dict[str, tuple[float, dict[str, Any]]] = {}
_overlays: dict[str, tuple[float, dict[str, Any]]] = {}


def _now_ms() -> float:
    return time.monotonic() * 1000


def _pack_key(tenant_id: str, year: int, month: int, interval: str = "1D") -> str:
    from app.domains.param_chart_constants import normalize_param_chart_interval

    iv = normalize_param_chart_interval(interval)
    # 1W / 1M are year-scoped aggregates — one pack per year.
    if iv in ("1M", "1W"):
        return f"atlas:param-chart:{tenant_id}:pack:{iv}:{year:04d}"
    return f"atlas:param-chart:{tenant_id}:pack:{iv}:{year:04d}-{month:02d}"


def _mem_pack_key(
    tenant_id: str, year: int, month: int, interval: str = "1D"
) -> str:
    from app.domains.param_chart_constants import normalize_param_chart_interval

    iv = normalize_param_chart_interval(interval)
    return f"{tenant_id}|{iv}|{year}|{month}"


def _watch_key(tenant_id: str) -> str:
    return f"atlas:param-chart:watch:{tenant_id}"


def _overlay_key(tenant_id: str) -> str:
    return f"atlas:param-chart:{tenant_id}:overlay"


def _metrics_gate_key(tenant_id: str, day: str) -> str:
    return f"atlas:param-chart:{tenant_id}:metrics-gate:{day}"


def _rebuild_lock_key(
    tenant_id: str, *, year: int, month: int, interval: str
) -> str:
    pack_suffix = _pack_key(tenant_id, year, month, interval).split(":pack:")[-1]
    return f"atlas:param-chart:{tenant_id}:rebuild:{pack_suffix}"


def reset_param_chart_cache_for_tests() -> None:
    _packs.clear()
    _watchers.clear()
    _overlays.clear()
    # metrics gates live only in Redis; tests use in-memory packs.


async def delete_month_pack(
    tenant_id: str, *, year: int, month: int, interval: str = "1D"
) -> None:
    """Drop a pack so the next explicit month load rebuilds (SSE must not stampede)."""
    key = _pack_key(tenant_id, year, month, interval)
    mem_key = _mem_pack_key(tenant_id, year, month, interval)
    _packs.pop(mem_key, None)
    client = await get_redis()
    if client is not None:
        try:
            await client.delete(key)
        except Exception:
            await invalidate_redis()
            logger.warning("param_chart_pack_delete_failed", tenant_id=tenant_id)


async def delete_month_packs_for_period(
    tenant_id: str, *, year: int, month: int
) -> None:
    """Drop every interval pack for a calendar month (strike/symbol changes)."""
    from app.domains.param_chart_constants import PARAM_CHART_INTERVAL_IDS

    for interval in PARAM_CHART_INTERVAL_IDS:
        await delete_month_pack(
            tenant_id, year=year, month=month, interval=interval
        )


async def try_rebuild_lock(
    tenant_id: str,
    *,
    year: int,
    month: int,
    interval: str,
    ttl_seconds: int = 90,
) -> bool:
    """Only one Kite year/month rebuild at a time per pack key."""
    client = await get_redis()
    key = _rebuild_lock_key(tenant_id, year=year, month=month, interval=interval)
    if client is not None:
        try:
            ok = await client.set(key, "1", ex=max(15, int(ttl_seconds)), nx=True)
            return bool(ok)
        except Exception:
            await invalidate_redis()
    # No Redis — allow (single-worker tests/dev).
    return True


async def release_rebuild_lock(
    tenant_id: str, *, year: int, month: int, interval: str
) -> None:
    client = await get_redis()
    if client is None:
        return
    key = _rebuild_lock_key(tenant_id, year=year, month=month, interval=interval)
    try:
        await client.delete(key)
    except Exception:
        await invalidate_redis()


async def get_month_pack(
    tenant_id: str, *, year: int, month: int, interval: str = "1D"
) -> dict[str, Any] | None:
    client = await get_redis()
    key = _pack_key(tenant_id, year, month, interval)
    mem_key = _mem_pack_key(tenant_id, year, month, interval)
    if client is not None:
        try:
            raw = await client.get(key)
            if raw is None:
                return None
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else None
        except Exception:
            await invalidate_redis()
            logger.warning("param_chart_pack_get_failed", tenant_id=tenant_id)

    entry = _packs.get(mem_key)
    if not entry:
        return None
    expires, payload = entry
    if expires <= _now_ms():
        _packs.pop(mem_key, None)
        return None
    return dict(payload)


async def set_month_pack(
    tenant_id: str,
    *,
    year: int,
    month: int,
    payload: dict[str, Any],
    interval: str = "1D",
) -> None:
    ttl = STALE_PACK_TTL_MS if payload.get("stale") else MONTH_PACK_TTL_MS
    key = _pack_key(tenant_id, year, month, interval)
    mem_key = _mem_pack_key(tenant_id, year, month, interval)
    client = await get_redis()
    if client is not None:
        try:
            await client.set(
                key,
                json.dumps(payload),
                px=ttl,
            )
        except Exception:
            await invalidate_redis()
            logger.warning("param_chart_pack_set_failed", tenant_id=tenant_id)

    _packs[mem_key] = (
        _now_ms() + ttl,
        dict(payload),
    )


async def touch_watcher(tenant_id: str, meta: dict[str, Any] | None = None) -> None:
    payload = dict(meta or {})
    payload["touched_at"] = int(time.time())
    client = await get_redis()
    if client is not None:
        try:
            await client.set(
                _watch_key(tenant_id),
                json.dumps(payload),
                ex=WATCH_TTL_SECONDS,
            )
            return
        except Exception:
            await invalidate_redis()
    _watchers[tenant_id] = (_now_ms() + WATCH_TTL_SECONDS * 1000, payload)


async def watcher_alive(tenant_id: str) -> bool:
    client = await get_redis()
    if client is not None:
        try:
            return bool(await client.exists(_watch_key(tenant_id)))
        except Exception:
            await invalidate_redis()
    entry = _watchers.get(tenant_id)
    if not entry:
        return False
    expires, _ = entry
    if expires <= _now_ms():
        _watchers.pop(tenant_id, None)
        return False
    return True


async def get_overlay(tenant_id: str) -> dict[str, Any] | None:
    """Lean today-overlay SSE frame (book + Signal snapshot)."""
    client = await get_redis()
    if client is not None:
        try:
            raw = await client.get(_overlay_key(tenant_id))
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict):
                    return data
        except Exception:
            await invalidate_redis()
            logger.warning("param_chart_overlay_get_failed", tenant_id=tenant_id)
    entry = _overlays.get(tenant_id)
    if not entry:
        return None
    expires, payload = entry
    if expires <= _now_ms():
        _overlays.pop(tenant_id, None)
        return None
    return dict(payload)


async def set_overlay(
    tenant_id: str,
    payload: dict[str, Any],
    *,
    ttl_ms: int = SNAPSHOT_TTL_MS,
) -> None:
    ttl = max(5_000, int(ttl_ms))
    client = await get_redis()
    if client is not None:
        try:
            await client.set(
                _overlay_key(tenant_id),
                json.dumps(payload),
                px=ttl,
            )
        except Exception:
            await invalidate_redis()
            logger.warning("param_chart_overlay_set_failed", tenant_id=tenant_id)
    _overlays[tenant_id] = (_now_ms() + ttl, dict(payload))


async def list_watched_tenant_ids() -> list[str]:
    """Tenants with an open Param Chart SSE desk."""
    client = await get_redis()
    if client is not None:
        try:
            cursor = 0
            tenants: list[str] = []
            pattern = "atlas:param-chart:watch:*"
            prefix = "atlas:param-chart:watch:"
            while True:
                cursor, keys = await client.scan(cursor=cursor, match=pattern, count=64)
                for key in keys:
                    if not str(key).startswith(prefix):
                        continue
                    tenants.append(str(key)[len(prefix) :])
                if cursor == 0:
                    break
            return tenants
        except Exception:
            await invalidate_redis()
            logger.warning("param_chart_watch_list_failed")
    now = _now_ms()
    alive: list[str] = []
    for tenant_id, (expires, _) in list(_watchers.items()):
        if expires <= now:
            _watchers.pop(tenant_id, None)
            continue
        alive.append(tenant_id)
    return alive


async def metrics_persist_due(tenant_id: str, *, day: str) -> bool:
    """True when a metrics persist should run (gate key absent). Does not acquire."""
    client = await get_redis()
    key = _metrics_gate_key(tenant_id, day)
    if client is not None:
        try:
            return await client.get(key) is None
        except Exception:
            await invalidate_redis()
            logger.warning("param_chart_metrics_gate_peek_failed", tenant_id=tenant_id)
    return True


async def eod_finalize_due(tenant_id: str, *, day: str) -> bool:
    """True when EOD finalize should run (gate key absent). Does not acquire."""
    client = await get_redis()
    key = f"atlas:param-chart:{tenant_id}:eod-final:{day}"
    if client is not None:
        try:
            return await client.get(key) is None
        except Exception:
            await invalidate_redis()
    return True


async def try_metrics_persist_gate(
    tenant_id: str,
    *,
    day: str,
    ttl_seconds: int = 300,
) -> bool:
    """Return True once per gate window (default 5m) so we don't rewrite packs every tick."""
    client = await get_redis()
    key = _metrics_gate_key(tenant_id, day)
    if client is not None:
        try:
            # SET NX — first writer wins for this window.
            ok = await client.set(key, "1", ex=max(30, int(ttl_seconds)), nx=True)
            return bool(ok)
        except Exception:
            await invalidate_redis()
            logger.warning("param_chart_metrics_gate_failed", tenant_id=tenant_id)
    # In-memory fallback for tests / no Redis: always allow.
    return True


async def try_eod_finalize_gate(tenant_id: str, *, day: str) -> bool:
    """One EOD finalize per calendar day after market close."""
    client = await get_redis()
    key = f"atlas:param-chart:{tenant_id}:eod-final:{day}"
    if client is not None:
        try:
            ok = await client.set(key, "1", ex=86_400, nx=True)
            return bool(ok)
        except Exception:
            await invalidate_redis()
    return True
