"""Domain workspace provisioning and dashboards."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import AgentConfig, Base, TeamConfig, Tenant, WorkflowConfig
from app.main import app


@pytest.fixture
async def domains_db(monkeypatch):
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    def make_session():
        return factory()

    for target in (
        "app.db.session.SessionFactory",
        "app.api.onboarding.SessionFactory",
        "app.auth.dependencies.SessionFactory",
    ):
        monkeypatch.setattr(target, make_session)

    yield factory
    await eng.dispose()


@pytest.mark.asyncio
async def test_self_serve_provisions_stock_broker_domain(domains_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/admin/onboarding/workspace",
            headers={
                "X-Dev-User-ID": "broker-admin",
                "X-Dev-Org-ID": "org_stock_broker",
                "X-Dev-Org-Role": "org:admin",
            },
            json={
                "name": "Acme Trading",
                "slug": "acme-trading",
                "domain": "stock_broker",
            },
        )
        assert created.status_code == 201, created.text
        payload = created.json()
        assert payload["domain"] == "stock_broker"

    async with domains_db() as session:
        tenant = await session.scalar(
            select(Tenant).where(Tenant.auth_org_id == "org_stock_broker")
        )
        assert tenant is not None
        assert tenant.domain == "stock_broker"
        agents = (
            await session.scalars(
                select(AgentConfig).where(AgentConfig.tenant_id == tenant.id)
            )
        ).all()
        teams = (
            await session.scalars(
                select(TeamConfig).where(TeamConfig.tenant_id == tenant.id)
            )
        ).all()
        workflows = (
            await session.scalars(
                select(WorkflowConfig).where(WorkflowConfig.tenant_id == tenant.id)
            )
        ).all()
        assert len(agents) == 3
        assert {row.slug for row in agents} == {
            "learning-guide",
            "paper-trader",
            "live-trader",
        }
        assert len(teams) == 3
        assert {row.slug for row in teams} == {
            "learning",
            "paper-trading",
            "live-trading",
        }
        assert len(workflows) == 2


@pytest.mark.asyncio
async def test_domain_dashboard_lists_stock_broker_widgets(domains_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/admin/onboarding/workspace",
            headers={
                "X-Dev-User-ID": "broker-admin",
                "X-Dev-Org-ID": "org_stock_broker_dash",
                "X-Dev-Org-Role": "org:admin",
            },
            json={
                "name": "Dash Trading",
                "slug": "dash-trading",
                "domain": "stock_broker",
            },
        )
    async with domains_db() as session:
        tenant = await session.scalar(
            select(Tenant).where(Tenant.auth_org_id == "org_stock_broker_dash")
        )
        assert tenant is not None
        tenant_id = str(tenant.id)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        dashboard = await client.get(
            "/admin/domains/dashboard",
            headers={
                "X-Dev-User-ID": "broker-admin",
                "X-Dev-Org-ID": "org_stock_broker_dash",
                "X-Dev-Org-Role": "org:admin",
                "X-Dev-Tenant-Id": tenant_id,
                "X-Dev-Role": "tenant_admin",
            },
        )
        assert dashboard.status_code == 200, dashboard.text
        body = dashboard.json()
        assert body["domain"] == "stock_broker"
        assert body["domain_label"] == "Stock Broker"
        assert body["fetched_at"]
        assert len(body["widgets"]) >= 6
        assert {row["id"] for row in body["widgets"]} >= {
            "desk_runs",
            "approval_queue",
            "live_approval",
            "paper_flow",
            "learning",
        }
        assert body["catalog"]["teams"] == 3
        assert [row["slug"] for row in body["chat_targets"]] == [
            "learning",
            "paper-trading",
            "live-trading",
        ]
        assert body["broker_tools"] == []
        assert any(row["id"] == "broker_tools" for row in body["widgets"])
        assert body["desk_snapshot"] is None

        snapshot = await client.get(
            "/admin/domains/dashboard",
            params={"desk_snapshot": "true"},
            headers={
                "X-Dev-User-ID": "broker-admin",
                "X-Dev-Org-ID": "org_stock_broker_dash",
                "X-Dev-Org-Role": "org:admin",
                "X-Dev-Tenant-Id": tenant_id,
                "X-Dev-Role": "tenant_admin",
            },
        )
        assert snapshot.status_code == 200, snapshot.text
        snap_body = snapshot.json()
        assert snap_body["desk_snapshot"]["tools"] == []
        assert any(
            row["id"] == "desk_broker" and row["value"] == "None"
            for row in snap_body["desk_snapshot"]["widgets"]
        )


def test_desk_snapshot_targets_live_and_paper_teams() -> None:
    from app.domains.desk_snapshot import DESK_TEAM_SLUGS, READ_CAPABILITIES

    assert DESK_TEAM_SLUGS == ("live-trading", "paper-trading")
    assert "get_user_margins" in READ_CAPABILITIES
    assert "get_user_margin" in READ_CAPABILITIES
