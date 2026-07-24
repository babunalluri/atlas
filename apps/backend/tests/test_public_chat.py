"""Public chat authz: published-only, tenant isolation, no draft leakage."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.rate_limit import limiter
from app.db.models import Base, Role, Tenant
from app.db.repositories import AgentRepository, TeamRepository, WorkflowRepository
from app.main import app
from app.tenancy.context import TenantContext


@pytest.fixture(autouse=True)
def _clear_rate_limiter():
    limiter._events.clear()
    limiter._inflight.clear()
    yield
    limiter._events.clear()
    limiter._inflight.clear()


@pytest.fixture
async def public_db(monkeypatch):
    """Bind app SessionFactory to a fresh in-memory schema with two tenants."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    tenant_a = Tenant(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        clerk_org_id="org_demo_acme",
        slug="acme",
        name="Acme Corp",
        branding={"primaryColor": "#0f766e"},
    )
    tenant_b = Tenant(
        id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        clerk_org_id="org_demo_globex",
        slug="globex",
        name="Globex Inc",
        branding={},
    )
    async with factory() as session:
        session.add_all([tenant_a, tenant_b])
        await session.commit()

    def make_session():
        return factory()

    for target in (
        "app.db.session.SessionFactory",
        "app.api.public.SessionFactory",
        "app.api.public_chat.SessionFactory",
        "app.api.onboarding.SessionFactory",
        "app.auth.dependencies.SessionFactory",
        "app.agent_runtime.agent_os.SessionFactory",
    ):
        monkeypatch.setattr(target, make_session)

    ctx_a = TenantContext(
        tenant_id=tenant_a.id,
        user_id="user-a",
        role=Role.tenant_admin,
        clerk_org_id=tenant_a.clerk_org_id,
    )
    ctx_b = TenantContext(
        tenant_id=tenant_b.id,
        user_id="user-b",
        role=Role.tenant_admin,
        clerk_org_id=tenant_b.clerk_org_id,
    )
    yield {"factory": factory, "tenant_a": ctx_a, "tenant_b": ctx_b}
    await eng.dispose()


async def _draft_agent(factory, tenant: TenantContext, slug: str):
    async with factory() as session:
        session.info["tenant_id"] = tenant.tenant_id
        repo = AgentRepository(session, tenant)
        config = await repo.create_config(slug=slug, name=slug.replace("-", " ").title())
        await repo.create_draft(
            config_id=config.id,
            instructions=f"Draft only {slug}",
            model_id="openai:gpt-4.1-mini",
            temperature=0.2,
        )
        await session.commit()
        return config


async def _published_agent(factory, tenant: TenantContext, slug: str):
    async with factory() as session:
        session.info["tenant_id"] = tenant.tenant_id
        repo = AgentRepository(session, tenant)
        config = await repo.create_config(slug=slug, name=slug.replace("-", " ").title())
        version = await repo.create_draft(
            config_id=config.id,
            instructions=f"Published {slug}",
            model_id="openai:gpt-4.1-mini",
            temperature=0.2,
        )
        await repo.publish(version.id)
        await session.commit()
        return config


@pytest.mark.asyncio
async def test_public_surface_requires_published_agent(public_db):
    factory = public_db["factory"]
    tenant_a = public_db["tenant_a"]
    await _draft_agent(factory, tenant_a, "draft-bot")
    await _published_agent(factory, tenant_a, "live-bot")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.get("/public/t/acme/agents/draft-bot")
        assert missing.status_code == 404

        live = await client.get("/public/t/acme/agents/live-bot")
        assert live.status_code == 200
        body = live.json()
        assert body["agent"]["slug"] == "live-bot"
        assert body["tenant"]["slug"] == "acme"
        assert "instructions" not in body["agent"]
        assert "model_id" not in body["agent"]


@pytest.mark.asyncio
async def test_public_surface_is_tenant_isolated(public_db):
    factory = public_db["factory"]
    await _published_agent(factory, public_db["tenant_a"], "acme-only")
    await _published_agent(factory, public_db["tenant_b"], "globex-only")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/public/t/acme/agents/acme-only")).status_code == 200
        assert (await client.get("/public/t/acme/agents/globex-only")).status_code == 404
        assert (await client.get("/public/t/globex/agents/globex-only")).status_code == 200


@pytest.mark.asyncio
async def test_public_agent_run_rejects_draft(public_db):
    await _draft_agent(public_db["factory"], public_db["tenant_a"], "still-draft")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/public/t/acme/agents/still-draft/runs",
            data={
                "message": "hello",
                "session_id": str(uuid.uuid4()),
                "stream": "true",
            },
            headers={"X-Guest-Id": "guest_test_12345678"},
        )
        assert response.status_code == 404
        assert "Published agent not found" in response.text


@pytest.mark.asyncio
async def test_public_agent_run_rejects_cross_tenant_slug(public_db):
    await _published_agent(public_db["factory"], public_db["tenant_b"], "secret-bot")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/public/t/acme/agents/secret-bot/runs",
            data={
                "message": "hello",
                "session_id": str(uuid.uuid4()),
                "stream": "true",
            },
            headers={"X-Guest-Id": "guest_test_abcdefgh"},
        )
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_public_team_and_workflow_surfaces_published_only(public_db):
    factory = public_db["factory"]
    tenant_a = public_db["tenant_a"]
    agent = await _published_agent(factory, tenant_a, "member-a")
    other = await _published_agent(factory, tenant_a, "member-b")

    async with factory() as session:
        session.info["tenant_id"] = tenant_a.tenant_id
        teams = TeamRepository(session, tenant_a)
        team = await teams.create_config(slug="front-line", name="Front Line")
        draft = await teams.create_draft(
            config_id=team.id,
            instructions="Coordinate",
            mode="coordinate",
            model_id="openai:gpt-4.1-mini",
            temperature=0.2,
            member_config_ids=[agent.id, other.id],
        )
        await teams.publish(draft.id)

        workflows = WorkflowRepository(session, tenant_a)
        workflow = await workflows.create_config(slug="intake", name="Intake")
        wf_draft = await workflows.create_draft(
            config_id=workflow.id,
            mode="sequential",
            steps=[
                {
                    "name": "Front",
                    "target_type": "team",
                    "target_config_id": team.id,
                }
            ],
        )
        await workflows.publish(wf_draft.id)

        draft_team = await teams.create_config(slug="unreleased", name="Unreleased")
        await teams.create_draft(
            config_id=draft_team.id,
            instructions="secret",
            mode="coordinate",
            model_id="openai:gpt-4.1-mini",
            temperature=0.2,
            member_config_ids=[agent.id, other.id],
        )
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        team_surface = await client.get("/public/t/acme/teams/front-line")
        assert team_surface.status_code == 200
        assert team_surface.json()["team"]["slug"] == "front-line"

        workflow_surface = await client.get("/public/t/acme/workflows/intake")
        assert workflow_surface.status_code == 200
        body = workflow_surface.json()
        assert body["workflow"]["slug"] == "intake"
        assert body["workflow"]["teams"][0]["slug"] == "front-line"

        assert (await client.get("/public/t/acme/teams/unreleased")).status_code == 404
