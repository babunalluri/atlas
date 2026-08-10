"""End users can run assigned workflows but cannot access builder APIs."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.models import Base, Role, Tenant
from app.main import app
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
        clerk_org_id="org_demo_acme",
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
            clerk_org_id=tenant.clerk_org_id,
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
