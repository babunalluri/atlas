"""End users can run assigned workflows but cannot access builder APIs."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.models import Base, Role, Tenant
from app.db.repositories import AgentRepository, SessionRepository
from app.main import app
from app.observability.repository import TraceRepository
from app.tenancy.context import TenantContext


@pytest.fixture
async def rbac_db(monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    tenant = Tenant(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        auth_org_id="org_demo_acme",
        slug="acme",
        name="Acme Corp",
        branding={"primaryColor": "#0f766e"},
    )
    async with factory() as session:
        session.add(tenant)
        await session.commit()

    def make_session():
        return factory()

    for target in (
        "app.db.session.SessionFactory",
        "app.auth.dependencies.SessionFactory",
        "app.agent_runtime.agent_os.SessionFactory",
    ):
        monkeypatch.setattr(target, make_session)

    yield {
        "factory": factory,
        "tenant": TenantContext(
            tenant_id=tenant.id,
            user_id="end-user-1",
            role=Role.end_user,
            auth_org_id=tenant.auth_org_id,
        ),
    }
    await eng.dispose()


@pytest.mark.asyncio
async def test_end_user_cannot_list_agent_catalog(rbac_db):
    tenant = rbac_db["tenant"]
    headers = {
        "x-dev-tenant-id": str(tenant.tenant_id),
        "x-dev-user-id": tenant.user_id,
        "x-dev-role": Role.end_user.value,
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.get("/admin/agents/catalog", headers=headers)
        assert denied.status_code == 403

        workspace = await client.get("/admin/workspace", headers=headers)
        assert workspace.status_code == 200
        body = workspace.json()
        assert body["role"] == Role.end_user.value
        assert body["can_administer"] is False
        assert body["slug"] == "acme"

        available = await client.get("/api/workflows/available", headers=headers)
        assert available.status_code == 200
        assert available.json() == []


@pytest.mark.asyncio
async def test_end_user_vault_is_own_metadata_only(rbac_db):
    tenant = rbac_db["tenant"]
    user_a = {
        "x-dev-tenant-id": str(tenant.tenant_id),
        "x-dev-user-id": "end-user-1",
        "x-dev-role": Role.end_user.value,
    }
    user_b = {
        "x-dev-tenant-id": str(tenant.tenant_id),
        "x-dev-user-id": "end-user-2",
        "x-dev-role": Role.end_user.value,
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.get("/admin/vault/users", headers=user_a)
        assert denied.status_code == 403

        created = await client.put(
            "/api/me/vault/access_token",
            headers=user_a,
            json={"value": "secret-a", "kind": "secret"},
        )
        assert created.status_code == 200
        body = created.json()
        assert body["name"] == "access_token"
        assert body["kind"] == "secret"
        assert "secret-a" not in str(body)
        assert "value" not in body
        assert "encrypted_value" not in body

        listed_a = await client.get("/api/me/vault", headers=user_a)
        assert listed_a.status_code == 200
        rows_a = listed_a.json()
        assert [row["name"] for row in rows_a] == ["access_token"]
        assert "secret-a" not in listed_a.text
        assert all("value" not in row for row in rows_a)

        listed_b = await client.get("/api/me/vault", headers=user_b)
        assert listed_b.status_code == 200
        assert listed_b.json() == []

        other = await client.put(
            "/api/me/vault/access_token",
            headers=user_b,
            json={"value": "secret-b", "kind": "secret"},
        )
        assert other.status_code == 200
        assert other.json()["name"] == "access_token"
        assert "secret-b" not in other.text

        still_a = await client.get("/api/me/vault", headers=user_a)
        assert [row["name"] for row in still_a.json()] == ["access_token"]
        assert "secret-b" not in still_a.text


async def _seed_user_trace(factory, tenant, user_id: str, *, slug: str, fail: bool = False):
    context = TenantContext(
        tenant_id=tenant.tenant_id,
        user_id=user_id,
        role=Role.end_user,
        auth_org_id=tenant.auth_org_id,
    )
    async with factory() as session:
        session.info["tenant_id"] = tenant.tenant_id
        agent_repo = AgentRepository(session, context)
        config = await agent_repo.create_config(slug=slug, name=f"Desk {slug}")
        version = await agent_repo.create_draft(
            config_id=config.id,
            instructions="Be useful",
            model_id="openai:gpt-4.1-mini",
            temperature=0.1,
        )
        await agent_repo.publish(version.id)
        conversation = await SessionRepository(session, context).pin(
            external_session_id=f"session-{slug}",
            agent_config_id=config.id,
            agent_version_id=version.id,
            runtime_session_id=f"tenant:{context.tenant_id}:session:{slug}",
            runtime_user_id=f"tenant:{context.tenant_id}:user:{user_id}",
            title=f"Chat {slug}",
        )
        traces = TraceRepository(session, context)
        trace = await traces.start(
            conversation=conversation,
            target_id=config.id,
            version_id=version.id,
            name="Desk run",
            message=f"hello from {user_id}",
        )
        if fail:
            await traces.record_event(
                trace.id, {"event": "RunError", "error": f"{user_id} failed"}
            )
        else:
            await traces.record_event(
                trace.id, {"event": "RunCompleted", "run_id": f"run-{slug}", "content": "ok"}
            )
        await session.commit()
        return trace.id


@pytest.mark.asyncio
async def test_end_user_traces_are_own_runs_only(rbac_db):
    tenant = rbac_db["tenant"]
    factory = rbac_db["factory"]
    user_a = {
        "x-dev-tenant-id": str(tenant.tenant_id),
        "x-dev-user-id": "end-user-1",
        "x-dev-role": Role.end_user.value,
    }
    user_b = {
        "x-dev-tenant-id": str(tenant.tenant_id),
        "x-dev-user-id": "end-user-2",
        "x-dev-role": Role.end_user.value,
    }

    trace_a = await _seed_user_trace(factory, tenant, "end-user-1", slug="user-a-run")
    trace_b = await _seed_user_trace(
        factory, tenant, "end-user-2", slug="user-b-run", fail=True
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin_denied = await client.get("/api/admin/traces", headers=user_a)
        assert admin_denied.status_code == 403

        listed_a = await client.get("/api/me/traces", headers=user_a)
        assert listed_a.status_code == 200
        rows_a = listed_a.json()
        assert [row["id"] for row in rows_a] == [str(trace_a)]
        assert rows_a[0]["user_id"] == "end-user-1"
        assert rows_a[0]["target_name"] == "Desk user-a-run"
        assert rows_a[0]["session_title"] == "Chat user-a-run"
        assert rows_a[0]["status"] == "completed"
        assert rows_a[0]["error"] is None
        assert str(trace_b) not in listed_a.text

        listed_b = await client.get("/api/me/traces", headers=user_b)
        assert listed_b.status_code == 200
        rows_b = listed_b.json()
        assert [row["id"] for row in rows_b] == [str(trace_b)]
        assert rows_b[0]["status"] == "error"
        assert rows_b[0]["error"] == "end-user-2 failed"
        assert str(trace_a) not in listed_b.text

        own = await client.get(f"/api/me/traces/{trace_a}", headers=user_a)
        assert own.status_code == 200
        body = own.json()
        assert body["id"] == str(trace_a)
        assert body["input"]["message"] == "hello from end-user-1"
        assert body["spans"]

        other = await client.get(f"/api/me/traces/{trace_b}", headers=user_a)
        assert other.status_code == 403

        missing = await client.get(
            f"/api/me/traces/{uuid.uuid4()}", headers=user_a
        )
        assert missing.status_code == 404
