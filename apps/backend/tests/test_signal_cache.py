"""Signal engine shared cache tests."""

from __future__ import annotations

import pytest

from app.domains import signal_engine_cache as cache
from app.domains.signal_engine import STREAM_INTERVAL_MS


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    cache.reset_signal_cache_for_tests()
    yield
    cache.reset_signal_cache_for_tests()


@pytest.mark.asyncio
async def test_metric_cache_roundtrip() -> None:
    await cache.set_metric("tenant-a", "feed", "fast", {"source": "live"})
    hit = await cache.get_metric("tenant-a", "feed")
    assert hit == {"source": "live"}


@pytest.mark.asyncio
async def test_snapshot_and_invalidate() -> None:
    payload = {"metrics": [], "poll_ms": STREAM_INTERVAL_MS}
    await cache.set_snapshot("tenant-a", payload)
    assert await cache.get_snapshot("tenant-a") == payload
    await cache.invalidate_tenant("tenant-a")
    assert await cache.get_snapshot("tenant-a") is None
    assert await cache.get_metric("tenant-a", "feed") is None


@pytest.mark.asyncio
async def test_watcher_tracking_in_memory() -> None:
    await cache.touch_watcher("tenant-a")
    tenants = await cache.list_watched_tenant_ids()
    assert "tenant-a" in tenants


@pytest.mark.asyncio
async def test_clear_watcher() -> None:
    await cache.touch_watcher("tenant-a")
    await cache.clear_watcher("tenant-a")
    assert "tenant-a" not in await cache.list_watched_tenant_ids()


@pytest.mark.asyncio
async def test_compute_lock_excludes_concurrent_local_holder() -> None:
    assert await cache.try_compute_lock("tenant-a") is True
    assert await cache.try_compute_lock("tenant-a") is False
    await cache.release_compute_lock("tenant-a")
    assert await cache.try_compute_lock("tenant-a") is True
    await cache.release_compute_lock("tenant-a")


@pytest.mark.asyncio
async def test_release_local_lock_when_redis_holds_no_token() -> None:
    """Local lock must release even when a Redis client is available."""
    assert await cache.try_compute_lock("tenant-a") is True
    await cache.release_compute_lock("tenant-a")
    assert await cache.try_compute_lock("tenant-a") is True
    await cache.release_compute_lock("tenant-a")


def test_stale_dated_session_fields_filters_old_keys() -> None:
    fields = [
        "straddle_session_open:2026-08-01",
        "straddle_session_open:2099-01-01",
        "options_lab:portfolios",
    ]
    stale = cache._stale_dated_session_fields(fields, max_age_days=14)
    assert "straddle_session_open:2026-08-01" in stale
    assert "straddle_session_open:2099-01-01" not in stale
    assert "options_lab:portfolios" not in stale
