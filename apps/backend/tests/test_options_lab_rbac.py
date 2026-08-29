"""End-user Lab SKU RBAC — Ideas / Backtest / paper Bots open to end_user.

Live bot placement, broker orders/GTTs, portfolios (Books) and config writes
stay admin-only. See docs/desk-architecture-roadmap.md (Track B).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, Tenant
from app.domains import options_lab_cache as ol_cache
from app.domains import signal_engine_cache as signal_cache
from app.domains.options_lab_bots import create_bot as create_bot_record
from app.domains.options_lab_bots import reset_bots_armed_for_tests
from app.main import app


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    ol_cache.reset_options_lab_cache_for_tests()
    signal_cache.reset_signal_cache_for_tests()
    reset_bots_armed_for_tests()
    yield
    ol_cache.reset_options_lab_cache_for_tests()
    signal_cache.reset_signal_cache_for_tests()
    reset_bots_armed_for_tests()


@pytest.fixture()
async def lab_db(monkeypatch):
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    def make_session():
        return factory()

    for target in (
        "app.db.session.SessionFactory",
        "app.api.options_lab.SessionFactory",
        "app.auth.dependencies.SessionFactory",
    ):
        monkeypatch.setattr(target, make_session)

    yield factory
    await eng.dispose()


async def _seed_tenant(lab_db, tenant_a) -> None:
    async with lab_db() as session:
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


def _headers(tenant_a, role: str) -> dict[str, str]:
    return {
        "x-dev-tenant-id": str(tenant_a.tenant_id),
        "x-dev-user-id": tenant_a.user_id,
        "x-dev-role": role,
    }


# Widened for the Lab SKU — end_user must not get 403.
TRADER_GETS = (
    "/admin/options-lab/screener?mode=fast",
    "/admin/options-lab/iv-history?symbol=NSE:NIFTY 50",
    "/admin/options-lab/backtests",
    "/admin/options-lab/bots",
)

# Must stay admin-only.
ADMIN_ONLY_GETS = (
    "/admin/options-lab/flows",
    "/admin/options-lab/gtts",
    "/admin/options-lab/portfolios",
    "/admin/options-lab/broker-reconcile",
)


@pytest.mark.asyncio
async def test_end_user_may_reach_trader_routes(lab_db, tenant_a) -> None:
    await _seed_tenant(lab_db, tenant_a)
    headers = _headers(tenant_a, "end_user")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for url in TRADER_GETS:
            res = await client.get(url, headers=headers)
            # Business logic may still fail without a broker binding; the RBAC
            # contract is only that the role is not rejected.
            assert res.status_code != 403, f"{url} denied for end_user"


@pytest.mark.asyncio
async def test_admin_only_routes_still_deny_end_user(lab_db, tenant_a) -> None:
    await _seed_tenant(lab_db, tenant_a)
    headers = _headers(tenant_a, "end_user")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for url in ADMIN_ONLY_GETS:
            res = await client.get(url, headers=headers)
            assert res.status_code == 403, f"{url} leaked to end_user"

        patched = await client.patch(
            "/admin/options-lab/config", headers=headers, json={"mock": True}
        )
        assert patched.status_code == 403

        ordered = await client.post(
            "/admin/options-lab/orders", headers=headers, json={"legs": []}
        )
        assert ordered.status_code == 403


@pytest.mark.asyncio
async def test_end_user_cannot_create_live_bot(lab_db, tenant_a) -> None:
    await _seed_tenant(lab_db, tenant_a)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.post(
            "/admin/options-lab/bots",
            headers=_headers(tenant_a, "end_user"),
            json={"name": "Live bot", "mode": "live", "template": "iron_condor"},
        )
        assert denied.status_code == 403

        # Paper is allowed for the same caller.
        allowed = await client.post(
            "/admin/options-lab/bots",
            headers=_headers(tenant_a, "end_user"),
            json={"name": "Paper bot", "mode": "paper", "template": "iron_condor"},
        )
        assert allowed.status_code != 403


@pytest.mark.asyncio
async def test_end_user_cannot_mutate_or_run_existing_live_bot(
    lab_db, tenant_a
) -> None:
    await _seed_tenant(lab_db, tenant_a)
    created = await create_bot_record(
        str(tenant_a.tenant_id),
        {"name": "Admin live bot", "mode": "live", "template": "iron_condor"},
    )
    assert created.get("ok"), created
    bot_id = created["bot"]["id"]

    headers = _headers(tenant_a, "end_user")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        patched = await client.patch(
            f"/admin/options-lab/bots/{bot_id}",
            headers=headers,
            json={"name": "hijacked"},
        )
        assert patched.status_code == 403

        deleted = await client.delete(
            f"/admin/options-lab/bots/{bot_id}", headers=headers
        )
        assert deleted.status_code == 403

        ran = await client.post(
            f"/admin/options-lab/bots/{bot_id}/run",
            headers=headers,
            json={"confirm": True},
        )
        assert ran.status_code == 403


@pytest.mark.asyncio
async def test_admin_may_still_create_live_bot(lab_db, tenant_a) -> None:
    await _seed_tenant(lab_db, tenant_a)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/admin/options-lab/bots",
            headers=_headers(tenant_a, "tenant_admin"),
            json={"name": "Live bot", "mode": "live", "template": "iron_condor"},
        )
        assert res.status_code != 403
