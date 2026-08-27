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
async def test_invalidate_underlying_keeps_slow_tiers() -> None:
    await cache.set_metric("tenant-a", "yahoo_global", "slow", {"a": 1})
    await cache.set_metric("tenant-a", "dow_jones", "slow", -0.2)
    await cache.set_metric("tenant-a", "levels", "medium", {"x": 1})
    await cache.set_metric("tenant-a", "option_chain", "medium", {"pcr": 1.1})
    await cache.set_snapshot("tenant-a", {"feed_source": "live"})
    await cache.invalidate_underlying_dependent("tenant-a")
    assert await cache.get_metric("tenant-a", "yahoo_global") == {"a": 1}
    assert await cache.get_metric("tenant-a", "dow_jones") == -0.2
    assert await cache.get_metric("tenant-a", "levels") is None
    assert await cache.get_metric("tenant-a", "option_chain") is None
    assert await cache.get_snapshot("tenant-a") is None


@pytest.mark.asyncio
async def test_invalidate_underlying_clears_instrument_scoped_metrics() -> None:
    await cache.set_metric("tenant-a", "levels:NSE:NIFTY_50", "medium", {"x": 1})
    await cache.set_metric("tenant-a", "trend:BSE:SENSEX", "medium", {"adx": 20})
    await cache.set_metric("tenant-a", "yahoo_global", "slow", {"ok": True})
    await cache.invalidate_underlying_dependent("tenant-a")
    assert await cache.get_metric("tenant-a", "levels:NSE:NIFTY_50") is None
    assert await cache.get_metric("tenant-a", "trend:BSE:SENSEX") is None
    assert await cache.get_metric("tenant-a", "yahoo_global") == {"ok": True}


@pytest.mark.asyncio
async def test_invalidate_underlying_clears_session_opens() -> None:
    await cache.set_session_value("tenant-a", "underlying_open:NSE:NIFTY_50:2026-08-26", 24500.0)
    await cache.set_session_value("tenant-a", "straddle_session_open:NSE:NIFTY_50:2026-08-26", 200.0)
    await cache.set_session_value("tenant-a", "options_lab_bots", {"keep": True})
    await cache.invalidate_underlying_dependent("tenant-a")
    assert await cache.get_session_value("tenant-a", "underlying_open:NSE:NIFTY_50:2026-08-26") is None
    assert await cache.get_session_value("tenant-a", "straddle_session_open:NSE:NIFTY_50:2026-08-26") is None
    assert await cache.get_session_value("tenant-a", "options_lab_bots") == {"keep": True}


@pytest.mark.asyncio
async def test_set_snapshot_refuses_stale_config_epoch() -> None:
    await cache.bump_config_epoch("tenant-a")  # -> 1
    await cache.bump_config_epoch("tenant-a")  # -> 2
    wrote = await cache.set_snapshot(
        "tenant-a",
        {"feed_source": "live", "config_epoch": 1, "metrics": []},
    )
    assert wrote is False
    assert await cache.get_snapshot("tenant-a") is None
    wrote = await cache.set_snapshot(
        "tenant-a",
        {"feed_source": "live", "config_epoch": 2, "metrics": []},
    )
    assert wrote is True
    assert (await cache.get_snapshot("tenant-a") or {}).get("config_epoch") == 2


@pytest.mark.asyncio
async def test_config_epoch_is_monotonic_without_ttl_reset() -> None:
    """Epoch must not live under m:* with SNAPSHOT_TTL — expiry disarms the guard."""
    a = await cache.bump_config_epoch("tenant-a")
    b = await cache.bump_config_epoch("tenant-a")
    assert b == a + 1
    # Full metric wipe must not reset the generation counter.
    await cache.invalidate_tenant("tenant-a")
    c = await cache.bump_config_epoch("tenant-a")
    assert c == b + 1


@pytest.mark.asyncio
async def test_config_epoch_survives_redis_blip(monkeypatch) -> None:
    """Pilot 05:14:34: Redis blip at tick start read epoch 0, snapshot later
    refused as 0 < 11 and 45s of work dropped. A blip must return last-known."""
    tenant = "tenant-epoch-blip"

    class GoodClient:
        def __init__(self) -> None:
            self.store: dict[str, int] = {}

        async def incr(self, key: str) -> int:
            self.store[key] = int(self.store.get(key, 0)) + 1
            return self.store[key]

        async def get(self, key: str) -> str | None:
            value = self.store.get(key)
            return None if value is None else str(value)

        async def set(self, key: str, value, **_kw) -> bool:
            self.store[key] = int(value)
            return True

    class BlipClient:
        async def get(self, key: str):
            raise ConnectionError("redis down")

        async def incr(self, key: str):
            raise ConnectionError("redis down")

    good = GoodClient()

    async def redis_good():
        return good

    async def redis_blip():
        return BlipClient()

    async def noop_invalidate():
        return None

    monkeypatch.setattr(cache, "invalidate_redis", noop_invalidate)
    monkeypatch.setattr(cache, "get_redis", redis_good)
    for _ in range(11):
        await cache.bump_config_epoch(tenant)
    assert await cache.get_config_epoch(tenant) == 11

    # Blip: reads return last-known (mirror), never 0.
    monkeypatch.setattr(cache, "get_redis", redis_blip)
    assert await cache.get_config_epoch(tenant) == 11
    # A bump during the blip stays monotonic locally.
    assert await cache.bump_config_epoch(tenant) == 12

    # Redis recovers having missed the blip bump: reads stay monotonic and the
    # next bump heals Redis forward instead of reissuing 12.
    monkeypatch.setattr(cache, "get_redis", redis_good)
    assert await cache.get_config_epoch(tenant) == 12
    assert await cache.bump_config_epoch(tenant) == 13
    assert await cache.get_config_epoch(tenant) == 13


@pytest.mark.asyncio
async def test_set_snapshot_not_refused_after_epoch_blip(monkeypatch) -> None:
    """End-to-end pilot chain: tick stamps the epoch read during a blip; the
    write must be accepted once Redis recovers (blip returns mirror, not 0)."""
    tenant = "tenant-epoch-blip-write"
    live = {"feed_source": "live", "metrics": [{"id": "atm"}], "evaluable": 40}

    for _ in range(11):
        await cache.bump_config_epoch(tenant)

    real_get_redis = cache.get_redis

    class BlipClient:
        async def get(self, key: str):
            raise ConnectionError("redis down")

    async def redis_blip():
        return BlipClient()

    async def noop_invalidate():
        return None

    monkeypatch.setattr(cache, "invalidate_redis", noop_invalidate)
    monkeypatch.setattr(cache, "get_redis", redis_blip)
    epoch_at_start = await cache.get_config_epoch(tenant)  # blip mid-read
    monkeypatch.setattr(cache, "get_redis", real_get_redis)

    assert epoch_at_start == 11  # not 0
    wrote = await cache.set_snapshot(tenant, {**live, "config_epoch": epoch_at_start})
    assert wrote is True


@pytest.mark.asyncio
async def test_watcher_tracking_in_memory() -> None:
    await cache.touch_watcher("tenant-a")
    tenants = await cache.list_watched_tenant_ids()
    assert "tenant-a" in tenants
    assert await cache.watcher_alive("tenant-a") is True
    assert await cache.watcher_alive("tenant-b") is False


@pytest.mark.asyncio
async def test_clear_watcher() -> None:
    await cache.touch_watcher("tenant-a")
    await cache.clear_watcher("tenant-a")
    assert "tenant-a" not in await cache.list_watched_tenant_ids()
    assert await cache.watcher_alive("tenant-a") is False


@pytest.mark.asyncio
async def test_compute_lock_excludes_concurrent_local_holder() -> None:
    assert await cache.try_compute_lock("tenant-a") is True
    assert await cache.try_compute_lock("tenant-a") is False
    await cache.release_compute_lock("tenant-a")
    assert await cache.try_compute_lock("tenant-a") is True
    await cache.release_compute_lock("tenant-a")


@pytest.mark.asyncio
async def test_extend_compute_lock_while_held() -> None:
    assert await cache.try_compute_lock("tenant-a") is True
    assert await cache.extend_compute_lock("tenant-a") is True
    await cache.release_compute_lock("tenant-a")
    assert await cache.extend_compute_lock("tenant-a") is False


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
