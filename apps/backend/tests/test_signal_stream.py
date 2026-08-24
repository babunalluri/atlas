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
        SNAPSHOT_FRESH_MS,
        SNAPSHOT_TTL_MS,
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
    tenant = "tenant-test"
    await cache.set_metric(tenant, "feed", "fast", {"source": "live"})
    await cache.set_snapshot(tenant, {"entry_ready": False, "metrics": []})
    await _invalidate_tenant_signal_cache(tenant)
    assert await cache.get_metric(tenant, "feed") is None
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


def test_apply_engine_stopped_overlay() -> None:
    out = _apply_engine_stopped_overlay(
        {"engine_enabled": True, "engine_active": True, "feed_source": "live"}
    )
    assert out["engine_enabled"] is False
    assert out["engine_active"] is False
    assert out["feed_source"] == "stopped"


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
async def test_signal_stream_denies_end_user(signals_db, tenant_a) -> None:
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
        denied = await client.get("/admin/signals/stream", headers=headers)
        assert denied.status_code == 403


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
