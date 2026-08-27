"""Signal engine stream + cache tests."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, Tenant
from app.domains import signal_engine_cache as cache
from app.domains.signal_engine import (
    BROKER_QUOTE_TTL_MS,
    STREAM_INTERVAL_MS,
    TIER_TTL_MS,
    _apply_engine_stopped_overlay,
    _invalidate_tenant_signal_cache,
)
from app.main import app
from app.tenancy.context import TenantContext


@pytest.fixture(autouse=True)
def _reset_signal_cache() -> None:
    cache.reset_signal_cache_for_tests()
    yield
    cache.reset_signal_cache_for_tests()


@pytest.fixture
async def signals_db(monkeypatch):
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    def make_session():
        return factory()

    for target in (
        "app.db.session.SessionFactory",
        "app.api.onboarding.SessionFactory",
        "app.api.public.SessionFactory",
        "app.api.signals.SessionFactory",
        "app.auth.dependencies.SessionFactory",
    ):
        monkeypatch.setattr(target, make_session)

    yield factory
    await eng.dispose()


def test_stream_and_broker_intervals() -> None:
    from app.domains.signal_engine_constants import (
        LOCK_HEARTBEAT_SECONDS,
        LOCK_TTL_MS,
        SIGNAL_TICK_DEADLINE_SECONDS,
        SNAPSHOT_FRESH_MS,
        SNAPSHOT_TTL_MS,
        STATE_COMPUTE_TIMEOUT_MS,
    )

    assert STREAM_INTERVAL_MS == 125
    assert BROKER_QUOTE_TTL_MS == 500
    assert TIER_TTL_MS["fast"] == STREAM_INTERVAL_MS
    assert TIER_TTL_MS["broker"] == BROKER_QUOTE_TTL_MS
    # Fresh window shorter than TTL so Stale is reachable while Redis still serves.
    assert SNAPSHOT_FRESH_MS < SNAPSHOT_TTL_MS
    assert SNAPSHOT_FRESH_MS >= STREAM_INTERVAL_MS * 8
    assert SNAPSHOT_TTL_MS > STREAM_INTERVAL_MS
    # Short lock + heartbeat: dead workers self-heal quickly.
    assert LOCK_TTL_MS == 10_000
    assert LOCK_HEARTBEAT_SECONDS * 1000 < LOCK_TTL_MS
    # Loop deadline must exceed a single state() timeout.
    assert SIGNAL_TICK_DEADLINE_SECONDS * 1000 > STATE_COMPUTE_TIMEOUT_MS


@pytest.mark.asyncio
async def test_snapshot_survives_beyond_stream_interval() -> None:
    """Worker/SSE must keep serving last-good while the next compute runs."""
    import asyncio

    from app.domains.signal_engine_constants import STREAM_INTERVAL_MS

    tenant = "tenant-ttl"
    await cache.set_snapshot(tenant, {"engine_active": True, "passed": 3})
    await asyncio.sleep((STREAM_INTERVAL_MS * 2) / 1000)
    hit = await cache.get_snapshot(tenant)
    assert hit is not None
    assert hit["passed"] == 3


@pytest.mark.asyncio
async def test_invalidate_tenant_signal_cache() -> None:
    """Config-patch invalidate is scoped — slow tiers stay warm."""
    tenant = "tenant-test"
    await cache.set_metric(tenant, "yahoo_global", "slow", {"ok": 1})
    await cache.set_metric(tenant, "india_vix", "medium", 14.2)
    await cache.set_metric(tenant, "levels", "medium", {"vwap": 1})
    await cache.set_metric(tenant, "setup", "medium", {"cfg": True})
    await cache.set_snapshot(tenant, {"entry_ready": False, "metrics": []})
    await _invalidate_tenant_signal_cache(tenant)
    assert await cache.get_metric(tenant, "yahoo_global") == {"ok": 1}
    assert await cache.get_metric(tenant, "india_vix") == 14.2
    assert await cache.get_metric(tenant, "levels") is None
    assert await cache.get_metric(tenant, "setup") is None
    assert await cache.get_snapshot(tenant) is None


@pytest.mark.asyncio
async def test_invalidate_tenant_full_wipes_slow_tiers() -> None:
    tenant = "tenant-full"
    await cache.set_metric(tenant, "yahoo_global", "slow", {"ok": 1})
    await cache.set_snapshot(tenant, {"x": 1})
    await _invalidate_tenant_signal_cache(tenant, full=True)
    assert await cache.get_metric(tenant, "yahoo_global") is None
    assert await cache.get_snapshot(tenant) is None


def _dev_headers(context: TenantContext) -> dict[str, str]:
    return {
        "x-dev-tenant-id": str(context.tenant_id),
        "x-dev-user-id": context.user_id,
        "x-dev-role": context.role.value,
    }


@pytest.mark.asyncio
async def test_stale_snapshot_overlaid_when_engine_stopped() -> None:
    tenant = "tenant-stopped"
    await cache.set_snapshot(
        tenant,
        {
            "engine_enabled": True,
            "engine_active": True,
            "live": True,
            "feed_source": "live",
            "passed": 11,
            "evaluable": 11,
        },
    )

    class _StubService:
        context = type("Ctx", (), {"tenant_id": tenant})()

        async def _load_config(self):
            from app.domains.signal_engine import SignalEngineConfig

            return SignalEngineConfig(engine_enabled=False)

    from app.domains.signal_engine import state_for_stream

    payload = await state_for_stream(_StubService())  # type: ignore[arg-type]
    assert payload["engine_enabled"] is False
    assert payload["engine_active"] is False
    assert payload["feed_source"] == "stopped"
    assert payload["passed"] == 11


@pytest.mark.asyncio
async def test_stream_frame_from_cache_fast_path() -> None:
    """Warm engine_enabled + snapshot must not need Postgres."""
    from app.domains.signal_engine import stream_frame_from_cache

    tenant = "tenant-fast"
    await cache.set_metric(tenant, "engine_enabled", "medium", True)
    await cache.set_snapshot(
        tenant,
        {
            "engine_enabled": True,
            "engine_active": True,
            "passed": 4,
            "computed_at_ms": 1_700_000_000_000,
        },
    )
    frame = await stream_frame_from_cache(tenant)
    assert frame is not None
    assert frame["passed"] == 4
    assert frame["engine_enabled"] is True
    watched = await cache.list_watched_tenant_ids()
    assert tenant in watched


@pytest.mark.asyncio
async def test_stream_frame_from_cache_misses_without_engine_flag() -> None:
    from app.domains.signal_engine import stream_frame_from_cache

    tenant = "tenant-miss"
    await cache.set_snapshot(tenant, {"passed": 1})
    assert await stream_frame_from_cache(tenant) is None


@pytest.mark.asyncio
async def test_stream_frame_from_cache_stopped_overlay() -> None:
    from app.domains.signal_engine import stream_frame_from_cache

    tenant = "tenant-stopped-fast"
    await cache.set_metric(tenant, "engine_enabled", "medium", False)
    await cache.set_snapshot(
        tenant,
        {
            "engine_enabled": True,
            "engine_active": True,
            "feed_source": "live",
            "passed": 9,
        },
    )
    frame = await stream_frame_from_cache(tenant)
    assert frame is not None
    assert frame["engine_enabled"] is False
    assert frame["feed_source"] == "stopped"
    assert frame["passed"] == 9
    watched = await cache.list_watched_tenant_ids()
    assert tenant not in watched


@pytest.mark.asyncio
async def test_state_for_stream_seeds_engine_enabled_metric() -> None:
    tenant = "tenant-seed"

    class _StubService:
        context = type("Ctx", (), {"tenant_id": tenant})()

        async def _load_config(self):
            from app.domains.signal_engine import SignalEngineConfig

            return SignalEngineConfig(engine_enabled=True)

        async def state(self, **_kwargs):
            raise AssertionError("should use starting/cold path without state()")

    from app.domains.signal_engine import state_for_stream

    # No snapshot → compute lock path; stub has no full state — seed metric first
    # via a warm snapshot so we only assert the metric write.
    await cache.set_snapshot(tenant, {"passed": 2, "engine_enabled": True})
    payload = await state_for_stream(_StubService())  # type: ignore[arg-type]
    assert payload["passed"] == 2
    assert await cache.get_metric(tenant, "engine_enabled") is True


def test_apply_engine_stopped_overlay() -> None:
    out = _apply_engine_stopped_overlay(
        {"engine_enabled": True, "engine_active": True, "feed_source": "live"}
    )
    assert out["engine_enabled"] is False
    assert out["engine_active"] is False
    assert out["feed_source"] == "stopped"


@pytest.mark.asyncio
async def test_engine_starting_payload_has_metric_skeleton() -> None:
    from app.domains.signal_engine import SignalEngineConfig, _engine_starting_payload

    payload = _engine_starting_payload(SignalEngineConfig(engine_enabled=True))
    assert payload["feed_source"] == "starting"
    assert payload["engine_computing"] is True
    assert len(payload["metrics"]) > 0
    assert all("id" in row and "label" in row for row in payload["metrics"])
    # No wall-clock / entry leak during warm-up.
    assert all(row.get("value") is None for row in payload["metrics"])
    assert all(row.get("passed") is None for row in payload["metrics"])
    assert payload["passed"] == 0
    assert payload["evaluable"] == 0
    assert payload["entry_ready"] is False
    assert "entry" not in payload


@pytest.mark.asyncio
async def test_seed_stream_cold_frame_does_not_compute_state() -> None:
    from app.domains.signal_engine import SignalEngineConfig, seed_stream_cold_frame

    tenant = "tenant-cold-seed"

    class _StubService:
        context = type(
            "Ctx",
            (),
            {"tenant_id": tenant, "auth_org_id": "org"},
        )()

        async def _load_config(self):
            return SignalEngineConfig(engine_enabled=True, underlying_symbol="NSE:NIFTY 50")

        async def state(self):
            raise AssertionError("SSE cold path must not await state()")

    payload, should_refresh = await seed_stream_cold_frame(_StubService())  # type: ignore[arg-type]
    assert should_refresh is True
    assert payload["feed_source"] == "starting"
    assert len(payload["metrics"]) > 0
    assert payload["entry_ready"] is False
    assert "entry" not in payload
    assert await cache.get_metric(tenant, "engine_enabled") is True
    snap = await cache.get_snapshot(tenant)
    assert snap is not None
    assert snap["feed_source"] == "starting"


@pytest.mark.asyncio
async def test_seed_stream_cold_frame_reuses_existing_snapshot() -> None:
    from app.domains.signal_engine import SignalEngineConfig, seed_stream_cold_frame

    tenant = "tenant-cold-reuse"
    await cache.set_snapshot(
        tenant,
        {
            "engine_enabled": True,
            "feed_source": "live",
            "passed": 7,
            "metrics": [{"id": "atm"}],
            "computed_at_ms": 1_700_000_000_000,
        },
    )

    class _StubService:
        context = type("Ctx", (), {"tenant_id": tenant, "auth_org_id": "org"})()

        async def _load_config(self):
            return SignalEngineConfig(engine_enabled=True)

        async def state(self):
            raise AssertionError("must not compute when snapshot warm")

    payload, should_refresh = await seed_stream_cold_frame(_StubService())  # type: ignore[arg-type]
    assert should_refresh is False
    assert payload["passed"] == 7
    assert payload["feed_source"] == "live"


@pytest.mark.asyncio
async def test_seed_stream_cold_frame_refreshes_stopped_while_enabled() -> None:
    from app.domains.signal_engine import SignalEngineConfig, seed_stream_cold_frame

    tenant = "tenant-stopped-enabled"
    await cache.set_snapshot(
        tenant,
        {
            "engine_enabled": False,
            "feed_source": "stopped",
            "passed": 0,
            "metrics": [],
            "computed_at_ms": 1_700_000_000_000,
        },
    )

    class _StubService:
        context = type("Ctx", (), {"tenant_id": tenant, "auth_org_id": "org"})()

        async def _load_config(self):
            return SignalEngineConfig(engine_enabled=True)

    _payload, should_refresh = await seed_stream_cold_frame(_StubService())  # type: ignore[arg-type]
    assert should_refresh is True


@pytest.mark.asyncio
async def test_broker_tools_memoized_on_service() -> None:
    from unittest.mock import MagicMock
    import uuid

    from app.db.models import Role
    from app.domains.signal_engine import SignalEngineService
    from app.tenancy.context import TenantContext

    session = MagicMock()
    session.info = {"tenant_id": uuid.uuid4()}
    ctx = TenantContext(
        tenant_id=session.info["tenant_id"],
        user_id="u",
        role=Role.tenant_admin,
        auth_org_id="org",
    )
    service = SignalEngineService(session, ctx)

    async def fake_iter():
        if False:  # pragma: no cover
            yield None
        return

    service._iter_signal_bindings = fake_iter  # type: ignore[method-assign]
    first = await service._broker_tools()
    second = await service._broker_tools()
    assert first is second
    assert first == []


@pytest.mark.asyncio
async def test_broker_tools_memo_dedupes_concurrent_builders() -> None:
    import asyncio
    from unittest.mock import MagicMock
    import uuid

    from app.db.models import Role
    from app.domains.signal_engine import SignalEngineService
    from app.tenancy.context import TenantContext

    session = MagicMock()
    session.info = {"tenant_id": uuid.uuid4()}
    ctx = TenantContext(
        tenant_id=session.info["tenant_id"],
        user_id="u",
        role=Role.tenant_admin,
        auth_org_id="org",
    )
    service = SignalEngineService(session, ctx)
    builds = 0
    peak = 0
    in_flight = 0

    async def slow_iter():
        nonlocal builds, peak, in_flight
        builds += 1
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        if False:  # pragma: no cover
            yield None
        return

    service._iter_signal_bindings = slow_iter  # type: ignore[method-assign]
    results = await asyncio.gather(
        service._broker_tools(),
        service._broker_tools(),
        service._broker_tools(),
    )
    assert builds == 1
    assert peak == 1
    assert results[0] is results[1] is results[2]


@pytest.mark.asyncio
async def test_auto_atm_persist_clears_setup_memo() -> None:
    """Persisting CE/PE must drop the setup cache or Fix 4 re-reads empties."""
    from unittest.mock import AsyncMock, MagicMock
    import uuid

    from app.db.models import Role
    from app.domains.signal_engine import SignalEngineConfig, SignalEngineService, _cache_set
    from app.tenancy.context import TenantContext

    tenant = str(uuid.uuid4())
    await _cache_set(
        tenant,
        "setup",
        "medium",
        {
            "settings": {
                "engine_enabled": True,
                "auto_atm_symbols": True,
                "underlying_symbol": "NSE:NIFTY 50",
                "nifty_fut_symbol": "NFO:NIFTY26SEPFUT",
                "ce_symbol": "",
                "pe_symbol": "",
            },
            "has_broker": True,
            "team_ready": True,
        },
    )
    assert await cache.get_metric(tenant, "setup") is not None

    session = MagicMock()
    session.info = {"tenant_id": uuid.UUID(tenant)}
    ctx = TenantContext(
        tenant_id=uuid.UUID(tenant),
        user_id="u",
        role=Role.tenant_admin,
        auth_org_id="org",
    )
    service = SignalEngineService(session, ctx)

    async def fake_load():
        return SignalEngineConfig(
            engine_enabled=True,
            auto_atm_symbols=True,
            underlying_symbol="NSE:NIFTY 50",
            nifty_fut_symbol="NFO:NIFTY26SEPFUT",
            ce_symbol="",
            pe_symbol="",
        )

    service._load_config = fake_load  # type: ignore[method-assign]
    service._signal_engine_tool = AsyncMock(return_value=MagicMock(id=uuid.uuid4()))  # type: ignore[method-assign]
    service._patch_tool_settings = AsyncMock(return_value={})  # type: ignore[method-assign]

    ok = await service.maybe_persist_auto_atm_symbols(
        {
            "ce_symbol": "NFO:NIFTY26SEP24500CE",
            "pe_symbol": "NFO:NIFTY26SEP24500PE",
        }
    )
    assert ok is True
    assert await cache.get_metric(tenant, "setup") is None
    service._patch_tool_settings.assert_awaited_once()


def test_annotate_snapshot_freshness_marks_stale_before_ttl() -> None:
    """Age past FRESH but under TTL must still be servable as stale."""
    import time

    from app.domains.signal_engine import _annotate_snapshot_freshness
    from app.domains.signal_engine_constants import SNAPSHOT_FRESH_MS, SNAPSHOT_TTL_MS

    assert SNAPSHOT_FRESH_MS < SNAPSHOT_TTL_MS
    # Just older than fresh window.
    computed = int(time.time() * 1000) - (SNAPSHOT_FRESH_MS + 100)
    out = _annotate_snapshot_freshness({"passed": 1, "computed_at_ms": computed})
    assert out["snapshot_stale"] is True
    assert out["data_age_ms"] is not None
    assert out["data_age_ms"] > SNAPSHOT_FRESH_MS


def test_annotate_snapshot_freshness_unknown_without_computed_at() -> None:
    from app.domains.signal_engine import _annotate_snapshot_freshness

    out = _annotate_snapshot_freshness({"passed": 1})
    assert out["data_age_ms"] is None
    assert out["snapshot_stale"] is None
    assert "computed_at_ms" not in out


def test_annotate_snapshot_freshness_while_computing() -> None:
    import time

    from app.domains.signal_engine import _annotate_snapshot_freshness
    from app.domains.signal_engine_constants import SNAPSHOT_FRESH_MS

    computed = int(time.time() * 1000) - (SNAPSHOT_FRESH_MS + 5_000)
    out = _annotate_snapshot_freshness(
        {"passed": 1, "computed_at_ms": computed},
        computing=True,
    )
    assert out["snapshot_stale"] is False
    assert out["engine_computing"] is True
    assert out["data_age_ms"] is not None and out["data_age_ms"] > SNAPSHOT_FRESH_MS


@pytest.mark.asyncio
async def test_signal_stream_requires_auth(client) -> None:
    denied = await client.get("/admin/signals/stream")
    assert denied.status_code == 401


@pytest.mark.asyncio
async def test_signal_stream_allows_end_user(signals_db, tenant_a) -> None:
    async with signals_db() as session:
        session.add(
            Tenant(
                id=tenant_a.tenant_id,
                auth_org_id=tenant_a.auth_org_id,
                slug="acme",
                name="Acme Corp",
                branding={},
            )
        )
        await session.commit()

    headers = {
        "x-dev-tenant-id": str(tenant_a.tenant_id),
        "x-dev-user-id": tenant_a.user_id,
        "x-dev-role": "end_user",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Avoid awaiting an infinite SSE body — probe auth via config/state GETs
        # which share ViewerContext with /stream.
        config = await client.get("/admin/signals/config", headers=headers)
        assert config.status_code == 200

        state = await client.get("/admin/signals/state", headers=headers)
        assert state.status_code == 200

        denied = await client.patch(
            "/admin/signals/config",
            headers=headers,
            json={"mock": True},
        )
        assert denied.status_code == 403

        publish = await client.post("/admin/signals/publish", headers=headers, json={})
        assert publish.status_code == 403


@pytest.mark.asyncio
async def test_signal_state_snapshot(signals_db, tenant_a) -> None:
    async with signals_db() as session:
        session.add(
            Tenant(
                id=tenant_a.tenant_id,
                auth_org_id=tenant_a.auth_org_id,
                slug="acme",
                name="Acme Corp",
                branding={},
            )
        )
        await session.commit()

    headers = _dev_headers(tenant_a)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/admin/signals/state", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert "metrics" in payload
        assert payload.get("stream") is True
        assert payload.get("poll_ms") == STREAM_INTERVAL_MS


@pytest.mark.asyncio
async def test_refresh_publishes_and_invalidates_on_poisoned_commit(monkeypatch) -> None:
    """Pilot 05:15:44: wait_for cancel mid-SQL broke the connection, COMMIT
    failed, and the computed frame was lost. The worker must still publish the
    frame and invalidate the poisoned session so the pool discards it."""
    import uuid

    from app.domains import signal_engine_worker as worker
    from app.domains.signal_engine import SignalEngineConfig

    tenant_id = uuid.uuid4()
    tenant_key = str(tenant_id)

    class PassTxn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FailingCommitTxn:
        """Body succeeds; COMMIT on exit raises (aborted PG transaction)."""

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            if exc_type is None:
                raise RuntimeError("RELEASE SAVEPOINT failed — connection poisoned")
            return False

    class FakeSession:
        def __init__(self) -> None:
            self.info: dict = {}
            self.invalidated = False

        def begin(self):
            return FailingCommitTxn()

        def begin_nested(self):
            return PassTxn()

        async def invalidate(self) -> None:
            self.invalidated = True

        async def flush(self) -> None:
            return None

    fake_session = FakeSession()

    class FakeSessionCM:
        async def __aenter__(self):
            return fake_session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class StubService:
        def __init__(self, session, context) -> None:
            self.session = session
            self.context = context

        async def _load_config(self) -> SignalEngineConfig:
            return SignalEngineConfig(
                engine_enabled=True, underlying_symbol="NSE:NIFTY 50"
            )

        async def maybe_persist_auto_atm_symbols(self, payload) -> bool:
            return False

    async def fake_guc(session, tenant) -> None:
        return None

    async def fake_sync(*args, **kwargs) -> bool:
        return False

    async def fake_compute(service, *, config, last_good=None):
        return {
            "feed_source": "live",
            "engine_enabled": True,
            "metrics": [{"id": "atm"}],
            "passed": 1,
            "evaluable": 1,
        }

    async def fake_tier_b(*args, **kwargs) -> None:
        return None

    async def not_due(*args, **kwargs) -> bool:
        return False

    monkeypatch.setattr(worker, "SessionFactory", lambda: FakeSessionCM())
    monkeypatch.setattr(worker, "apply_tenant_guc", fake_guc)
    monkeypatch.setattr(worker, "SignalEngineService", StubService)
    monkeypatch.setattr(worker, "sync_kite_for_signal_tenant", fake_sync)
    monkeypatch.setattr(worker, "_compute_state_payload", fake_compute)
    monkeypatch.setattr(worker, "refresh_tier_b_for_tenant", fake_tier_b)
    monkeypatch.setattr(
        "app.domains.param_chart_cache.metrics_persist_due", not_due
    )
    monkeypatch.setattr(
        "app.domains.param_chart_cache.eod_finalize_due", not_due
    )

    ok = await worker.refresh_tenant_snapshot(tenant_id, auth_org_id="org-1")

    assert ok is True
    # Poisoned connection dropped from the pool, not recycled.
    assert fake_session.invalidated is True
    # The computed frame still reached Redis/local — desk does not stick on STARTING.
    snap = await cache.get_snapshot(tenant_key)
    assert snap is not None
    assert snap["feed_source"] == "live"
    assert snap.get("computed_at_ms") is not None
