"""Domain workspace provisioning and dashboards."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import (
    AgentConfig,
    Base,
    Role,
    TeamAssignment,
    TeamConfig,
    Tenant,
    WorkflowConfig,
)
from app.auth.identity_admin import ProvisionedIdentity
from app.db.repositories import TeamRepository
from app.domains.access import assign_domain_default_teams
from app.domains.dashboard import order_desk_chat_targets
from app.domains.templates import STOCK_BROKER
from app.domains.types import DOMAIN_DEFAULT_TEAM_SLUGS, STOCK_BROKER_DESK_TEAMS
from app.main import app
from app.tenancy.context import TenantContext


def test_stock_broker_pack_includes_research_and_auto_assign() -> None:
    assert STOCK_BROKER_DESK_TEAMS == (
        "learning",
        "paper-trading",
        "live-trading",
        "research",
    )
    assert DOMAIN_DEFAULT_TEAM_SLUGS["stock_broker"] == STOCK_BROKER_DESK_TEAMS
    assert {row.slug for row in STOCK_BROKER.agents} >= {
        "learning-guide",
        "paper-trader",
        "live-trader",
    }
    assert "researcher" not in {row.slug for row in STOCK_BROKER.agents}
    assert {row.slug for row in STOCK_BROKER.teams} >= {
        "learning",
        "paper-trading",
        "live-trading",
        "research",
    }
    # Research is leader-only: no member agent, so the team itself must carry the
    # tool-required rules and run in a mode that does not route to members.
    research = next(row for row in STOCK_BROKER.teams if row.slug == "research")
    assert research.member_slugs == []
    assert research.mode == "coordinate"
    assert "MUST call tools" in research.instructions
    assert "research_option_payoff" in research.instructions
    assert "place_order" in research.instructions
    assert "Never invent" in research.instructions


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
        "app.api.public.SessionFactory",
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
        assert len(teams) == 4
        assert {row.slug for row in teams} == {
            "learning",
            "paper-trading",
            "live-trading",
            "research",
        }
        assert len(workflows) == 2
        assignments = (
            await session.scalars(
                select(TeamAssignment).where(
                    TeamAssignment.tenant_id == tenant.id,
                    TeamAssignment.user_id == "broker-admin",
                )
            )
        ).all()
        assert len(assignments) == 4


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
        assert body["catalog"]["teams"] == 4
        assert [row["slug"] for row in body["chat_targets"]] == [
            "learning",
            "paper-trading",
            "live-trading",
            "research",
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
        book_tabs = {row["tab"] for row in snap_body["desk_snapshot"]["books"]}
        assert {"orders", "positions", "holdings", "watchlist"} <= book_tabs
        orders = next(
            row for row in snap_body["desk_snapshot"]["books"] if row["id"] == "orders"
        )
        assert orders["rows"] == []
        assert orders["columns"]
        assert orders["error"] is None


@pytest.mark.asyncio
async def test_end_user_gets_stock_broker_customer_desk(domains_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/admin/onboarding/workspace",
            headers={
                "X-Dev-User-ID": "broker-admin",
                "X-Dev-Org-ID": "org_stock_broker_end",
                "X-Dev-Org-Role": "org:admin",
            },
            json={
                "name": "Retail Desk",
                "slug": "retail-desk",
                "domain": "stock_broker",
            },
        )
    async with domains_db() as session:
        tenant = await session.scalar(
            select(Tenant).where(Tenant.auth_org_id == "org_stock_broker_end")
        )
        assert tenant is not None
        tenant_id = str(tenant.id)
        await _assign_default_desk_teams(session, tenant, "retail-trader")

    headers = {
        "X-Dev-User-ID": "retail-trader",
        "X-Dev-Org-ID": "org_stock_broker_end",
        "X-Dev-Tenant-Id": tenant_id,
        "X-Dev-Role": "end_user",
    }
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.get(
            "/admin/domains/dashboard",
            headers=headers,
        )
        assert denied.status_code == 403

        branding = await client.get("/public/tenants/retail-desk")
        assert branding.status_code == 200, branding.text
        assert branding.json()["domain"] == "stock_broker"

        desk = await client.get("/api/desk", headers=headers)
        assert desk.status_code == 200, desk.text
        body = desk.json()
        assert [row["slug"] for row in body["chat_targets"]] == [
            "learning",
            "paper-trading",
            "live-trading",
            "research",
        ]
        assert body["quick_links"] == []
        assert {row["id"] for row in body["widgets"]} <= {
            "learning",
            "paper_flow",
            "live_approval",
            "research",
            "broker_tools",
            "desk_broker",
        }
        assert body["desk_snapshot"] is None

        snap = await client.get(
            "/api/desk",
            params={"desk_snapshot": "true"},
            headers=headers,
        )
        assert snap.status_code == 200, snap.text
        snap_body = snap.json()
        assert snap_body["desk_snapshot"] is not None
        book_tabs = {row["tab"] for row in snap_body["desk_snapshot"]["books"]}
        assert {"orders", "positions", "holdings", "watchlist"} <= book_tabs
        assert all(
            "rows" in row and "columns" in row
            for row in snap_body["desk_snapshot"]["books"]
        )

    async with domains_db() as session:
        assigned = (
            await session.scalars(
                select(TeamAssignment).where(
                    TeamAssignment.tenant_id == tenant.id,
                    TeamAssignment.user_id == "retail-trader",
                )
            )
        ).all()
        assert len(assigned) == 4


def test_desk_snapshot_targets_live_and_paper_teams() -> None:
    from app.domains.desk_snapshot import DESK_TEAM_SLUGS, READ_CAPABILITIES

    assert DESK_TEAM_SLUGS == ("live-trading", "paper-trading")
    assert "get_user_margins" in READ_CAPABILITIES
    assert "get_user_margin" in READ_CAPABILITIES
    assert "list_orders" in READ_CAPABILITIES
    assert "get_holdings" in READ_CAPABILITIES


def test_order_desk_chat_targets_keeps_pack_order_then_named_extras() -> None:
    rows = [
        {"id": "4", "slug": "options-lab", "name": "Options lab", "published": True},
        {"id": "3", "slug": "live-trading", "name": "Live trading", "published": True},
        {"id": "6", "slug": "research", "name": "Research", "published": True},
        {"id": "1", "slug": "learning", "name": "Learning", "published": True},
        {"id": "2", "slug": "paper-trading", "name": "Paper trading", "published": True},
        {"id": "5", "slug": "alpha-desk", "name": "Alpha desk", "published": True},
    ]
    assert [row["slug"] for row in order_desk_chat_targets(rows)] == [
        "learning",
        "paper-trading",
        "live-trading",
        "research",
        "alpha-desk",
        "options-lab",
    ]
    assert [row["name"] for row in order_desk_chat_targets(rows)] == [
        "Learning",
        "Paper trading",
        "Live trading",
        "Research",
        "Alpha desk",
        "Options lab",
    ]


def test_order_desk_chat_targets_empty() -> None:
    assert order_desk_chat_targets([]) == []


def test_order_desk_chat_targets_drops_unpublished() -> None:
    rows = [
        {"id": "1", "slug": "learning", "name": "Learning", "published": True},
        {"id": "d", "slug": "draft-desk", "name": "Draft desk", "published": False},
        {"id": "2", "slug": "paper-trading", "name": "Paper trading", "published": True},
        {"id": "x", "slug": "alpha-desk", "name": "Alpha desk", "published": False},
    ]
    assert [row["slug"] for row in order_desk_chat_targets(rows)] == [
        "learning",
        "paper-trading",
    ]


async def _provision_stock_broker(client, *, org_id: str, slug: str, name: str):
    created = await client.post(
        "/admin/onboarding/workspace",
        headers={
            "X-Dev-User-ID": "broker-admin",
            "X-Dev-Org-ID": org_id,
            "X-Dev-Org-Role": "org:admin",
        },
        json={"name": name, "slug": slug, "domain": "stock_broker"},
    )
    assert created.status_code == 201, created.text
    return created.json()


def _admin_headers(tenant_id: str, org_id: str) -> dict[str, str]:
    return {
        "X-Dev-User-ID": "broker-admin",
        "X-Dev-Org-ID": org_id,
        "X-Dev-Org-Role": "org:admin",
        "X-Dev-Tenant-Id": tenant_id,
        "X-Dev-Role": "tenant_admin",
    }


def _end_user_headers(tenant_id: str, org_id: str, user_id: str) -> dict[str, str]:
    return {
        "X-Dev-User-ID": user_id,
        "X-Dev-Org-ID": org_id,
        "X-Dev-Tenant-Id": tenant_id,
        "X-Dev-Role": "end_user",
    }


async def _assign_default_desk_teams(session, tenant: Tenant, user_id: str) -> list[str]:
    admin = TenantContext(
        tenant_id=tenant.id,
        user_id="broker-admin",
        role=Role.tenant_admin,
        auth_org_id=tenant.auth_org_id,
    )
    assigned = await assign_domain_default_teams(session, admin, user_id)
    await session.commit()
    return assigned


@pytest.mark.asyncio
async def test_customer_desk_tabs_follow_assigned_teams(domains_db):
    org_id = "org_stock_broker_tabs"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _provision_stock_broker(
            client, org_id=org_id, slug="tabs-desk", name="Tabs Desk"
        )

    async with domains_db() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.auth_org_id == org_id))
        assert tenant is not None
        tenant_id = str(tenant.id)
        admin = TenantContext(
            tenant_id=tenant.id,
            user_id="broker-admin",
            role=Role.tenant_admin,
            auth_org_id=org_id,
        )
        teams = TeamRepository(session, admin)
        live = await teams.get_config_by_slug("live-trading")
        assert live is not None
        live.published_version_id = None
        await session.commit()
        await _assign_default_desk_teams(session, tenant, "two-tab-trader")

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        desk = await client.get(
            "/api/desk",
            headers=_end_user_headers(tenant_id, org_id, "two-tab-trader"),
        )
        assert desk.status_code == 200, desk.text
        body = desk.json()
        assert [row["slug"] for row in body["chat_targets"]] == [
            "learning",
            "paper-trading",
            "research",
        ]
        assert [row["name"] for row in body["chat_targets"]] == [
            "Learning",
            "Paper trading",
            "Research",
        ]

    async with domains_db() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.auth_org_id == org_id))
        assert tenant is not None
        for slug in ("learning", "paper-trading"):
            team = await session.scalar(
                select(TeamConfig).where(
                    TeamConfig.tenant_id == tenant.id, TeamConfig.slug == slug
                )
            )
            assert team is not None
            team.published_version_id = None
        await session.commit()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        empty = await client.get(
            "/api/desk",
            headers=_end_user_headers(tenant_id, org_id, "empty-trader"),
        )
        assert empty.status_code == 200, empty.text
        assert empty.json()["chat_targets"] == []


@pytest.mark.asyncio
async def test_customer_desk_includes_extra_assigned_team_by_name(domains_db):
    org_id = "org_stock_broker_extra"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _provision_stock_broker(
            client, org_id=org_id, slug="extra-desk", name="Extra Desk"
        )

    async with domains_db() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.auth_org_id == org_id))
        assert tenant is not None
        tenant_id = str(tenant.id)

    admin_headers = _admin_headers(tenant_id, org_id)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get("/admin/teams", headers=admin_headers)
        assert listed.status_code == 200, listed.text
        learning = next(row for row in listed.json() if row["slug"] == "learning")
        cloned = await client.post(
            f"/admin/teams/{learning['id']}/clone", headers=admin_headers
        )
        assert cloned.status_code == 201, cloned.text
        extra_id = cloned.json()["id"]
        published = await client.post(
            f"/admin/teams/{extra_id}/publish", headers=admin_headers
        )
        assert published.status_code == 200, published.text
        renamed = await client.patch(
            f"/admin/teams/{extra_id}",
            headers=admin_headers,
            json={"name": "Options lab"},
        )
        assert renamed.status_code == 200, renamed.text

    async with domains_db() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.auth_org_id == org_id))
        assert tenant is not None
        admin = TenantContext(
            tenant_id=tenant.id,
            user_id="broker-admin",
            role=Role.tenant_admin,
            auth_org_id=org_id,
        )
        teams = TeamRepository(session, admin)
        extra = await teams.get_config(uuid.UUID(extra_id))
        assert extra is not None
        await assign_domain_default_teams(session, admin, "four-tab-trader")
        await teams.ensure_user_assignments("four-tab-trader", [extra.id])
        await session.commit()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        desk = await client.get(
            "/api/desk",
            headers=_end_user_headers(tenant_id, org_id, "four-tab-trader"),
        )
        assert desk.status_code == 200, desk.text
        body = desk.json()
        assert [row["slug"] for row in body["chat_targets"]] == [
            "learning",
            "paper-trading",
            "live-trading",
            "research",
            "learning-copy",
        ]
        assert [row["name"] for row in body["chat_targets"]] == [
            "Learning",
            "Paper trading",
            "Live trading",
            "Research",
            "Options lab",
        ]
        dashboard = await client.get(
            "/admin/domains/dashboard",
            headers=admin_headers,
        )
        assert dashboard.status_code == 200, dashboard.text
        assert [row["slug"] for row in dashboard.json()["chat_targets"]] == [
            "learning",
            "paper-trading",
            "live-trading",
            "research",
        ]
        assert "learning-copy" not in [
            row["slug"] for row in dashboard.json()["chat_targets"]
        ]


@pytest.mark.asyncio
async def test_admin_desk_chat_only_assigned_published_teams(domains_db):
    org_id = "org_stock_broker_admin_tabs"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _provision_stock_broker(
            client, org_id=org_id, slug="admin-tabs-desk", name="Admin Tabs Desk"
        )

    async with domains_db() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.auth_org_id == org_id))
        assert tenant is not None
        tenant_id = str(tenant.id)

    admin_headers = _admin_headers(tenant_id, org_id)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get("/admin/teams", headers=admin_headers)
        assert listed.status_code == 200, listed.text
        learning = next(row for row in listed.json() if row["slug"] == "learning")
        draft_slugs: list[str] = []
        for _ in range(8):
            cloned = await client.post(
                f"/admin/teams/{learning['id']}/clone", headers=admin_headers
            )
            assert cloned.status_code == 201, cloned.text
            body = cloned.json()
            draft_slugs.append(body["slug"])
            assert body.get("published_version_id") in (None, "")
        extra = await client.post(
            f"/admin/teams/{learning['id']}/clone", headers=admin_headers
        )
        assert extra.status_code == 201, extra.text
        extra_id = extra.json()["id"]
        extra_slug = extra.json()["slug"]
        published = await client.post(
            f"/admin/teams/{extra_id}/publish", headers=admin_headers
        )
        assert published.status_code == 200, published.text
        renamed = await client.patch(
            f"/admin/teams/{extra_id}",
            headers=admin_headers,
            json={"name": "Options lab"},
        )
        assert renamed.status_code == 200, renamed.text

        dashboard = await client.get(
            "/admin/domains/dashboard", headers=admin_headers
        )
        assert dashboard.status_code == 200, dashboard.text
        body = dashboard.json()
        assert body["catalog"]["teams"] >= 12
        assert [row["slug"] for row in body["chat_targets"]] == [
            "learning",
            "paper-trading",
            "live-trading",
            "research",
        ]
        chat_slugs = {row["slug"] for row in body["chat_targets"]}
        assert extra_slug not in chat_slugs
        assert chat_slugs.isdisjoint(draft_slugs)
        assert all(row["published"] for row in body["chat_targets"])

        desk = await client.get("/api/desk", headers=admin_headers)
        assert desk.status_code == 200, desk.text
        assert [row["slug"] for row in desk.json()["chat_targets"]] == [
            "learning",
            "paper-trading",
            "live-trading",
            "research",
        ]

    async with domains_db() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.auth_org_id == org_id))
        assert tenant is not None
        admin = TenantContext(
            tenant_id=tenant.id,
            user_id="broker-admin",
            role=Role.tenant_admin,
            auth_org_id=org_id,
        )
        teams = TeamRepository(session, admin)
        live = await teams.get_config_by_slug("live-trading")
        extra_team = await teams.get_config(uuid.UUID(extra_id))
        assert live is not None and extra_team is not None
        kept = [
            team_id
            for team_id in await teams.assigned_team_ids("broker-admin")
            if team_id != live.id
        ]
        await teams.replace_user_assignments("broker-admin", kept)
        await teams.ensure_user_assignments("broker-admin", [extra_team.id])
        await session.commit()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        dashboard = await client.get(
            "/admin/domains/dashboard", headers=admin_headers
        )
        assert dashboard.status_code == 200, dashboard.text
        assert [row["slug"] for row in dashboard.json()["chat_targets"]] == [
            "learning",
            "paper-trading",
            "research",
            extra_slug,
        ]
        assert [row["name"] for row in dashboard.json()["chat_targets"]] == [
            "Learning",
            "Paper trading",
            "Research",
            "Options lab",
        ]
        assert "live-trading" not in [
            row["slug"] for row in dashboard.json()["chat_targets"]
        ]


class _FakeIdentity:
    def __init__(self, settings=None, **kwargs):
        del settings, kwargs

    def configured(self) -> bool:
        return True

    async def provision_tenant_user(self, **kwargs):
        return ProvisionedIdentity(
            user_id=f"kc-{kwargs['email']}",
            email=kwargs["email"],
            invite_pending=False,
        )

    async def delete_user(self, user_id: str) -> None:
        del user_id


@pytest.mark.asyncio
async def test_create_user_auto_assigns_desk_teams_and_update_can_unassign(
    domains_db, monkeypatch
):
    monkeypatch.setattr("app.api.users.IdentityAdminClient", _FakeIdentity)
    org_id = "org_stock_broker_unassign"
    password = "trader-pass-1"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _provision_stock_broker(
            client, org_id=org_id, slug="unassign-desk", name="Unassign Desk"
        )

    async with domains_db() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.auth_org_id == org_id))
        assert tenant is not None
        tenant_id = str(tenant.id)

    admin_headers = _admin_headers(tenant_id, org_id)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/admin/users",
            headers=admin_headers,
            json={
                "email": "trader@unassign.test",
                "display_name": "Unassign Trader",
                "role": "end_user",
                "password": password,
                "password_confirm": password,
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        membership_id = body["id"]
        user_id = body["user_id"]
        team_ids = body["team_ids"]
        assert len(team_ids) == 4

        kept = team_ids[0]
        updated = await client.patch(
            f"/admin/users/{membership_id}",
            headers=admin_headers,
            json={"team_ids": [kept]},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["team_ids"] == [kept]

        desk = await client.get(
            "/api/desk",
            headers=_end_user_headers(tenant_id, org_id, user_id),
        )
        assert desk.status_code == 200, desk.text
        assert [row["id"] for row in desk.json()["chat_targets"]] == [kept]

        fetched = await client.get(
            f"/admin/users/{membership_id}",
            headers=admin_headers,
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["team_ids"] == [kept]
