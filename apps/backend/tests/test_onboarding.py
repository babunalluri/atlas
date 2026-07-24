"""Self-serve workspace onboarding for unprovisioned Clerk orgs."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, PlatformAuditEvent, Tenant
from app.main import app


@pytest.fixture
async def onboarding_db(monkeypatch):
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
async def test_self_serve_creates_workspace_from_dev_org(onboarding_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        status = await client.get(
            "/admin/onboarding/status",
            headers={
                "X-Dev-User-ID": "new-admin",
                "X-Dev-Org-ID": "org_brand_new",
                "X-Dev-Org-Role": "org:admin",
            },
        )
        assert status.status_code == 200
        body = status.json()
        assert body["provisioned"] is False
        assert body["can_create"] is True
        assert body["org_id"] == "org_brand_new"

        created = await client.post(
            "/admin/onboarding/workspace",
            headers={
                "X-Dev-User-ID": "new-admin",
                "X-Dev-Org-ID": "org_brand_new",
                "X-Dev-Org-Role": "org:admin",
            },
            json={"name": "Brand New Co", "slug": "brand-new"},
        )
        assert created.status_code == 201
        payload = created.json()
        assert payload["slug"] == "brand-new"
        assert payload["clerk_org_id"] == "org_brand_new"

        again = await client.post(
            "/admin/onboarding/workspace",
            headers={
                "X-Dev-User-ID": "new-admin",
                "X-Dev-Org-ID": "org_brand_new",
                "X-Dev-Org-Role": "org:admin",
            },
            json={"name": "Brand New Co", "slug": "brand-new-2"},
        )
        assert again.status_code == 409

    async with onboarding_db() as session:
        tenant = await session.scalar(
            select(Tenant).where(Tenant.clerk_org_id == "org_brand_new")
        )
        assert tenant is not None
        events = (
            await session.scalars(
                select(PlatformAuditEvent).where(PlatformAuditEvent.tenant_id == tenant.id)
            )
        ).all()
        assert any(event.action == "tenant.self_serve.create" for event in events)


@pytest.mark.asyncio
async def test_self_serve_requires_org_admin(onboarding_db):
    del onboarding_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/admin/onboarding/workspace",
            headers={
                "X-Dev-User-ID": "member",
                "X-Dev-Org-ID": "org_member_only",
                "X-Dev-Org-Role": "org:member",
            },
            json={"name": "Nope", "slug": "nope"},
        )
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_self_serve_ignores_body_clerk_org_id(onboarding_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/admin/onboarding/workspace",
            headers={
                "X-Dev-User-ID": "new-admin",
                "X-Dev-Org-ID": "org_from_claims",
                "X-Dev-Org-Role": "org:admin",
            },
            json={
                "name": "From Claims",
                "slug": "from-claims",
                "clerk_org_id": "org_spoofed",
            },
        )
        assert created.status_code == 201
        assert created.json()["clerk_org_id"] == "org_from_claims"

    async with onboarding_db() as session:
        spoofed = await session.scalar(
            select(Tenant).where(Tenant.clerk_org_id == "org_spoofed")
        )
        assert spoofed is None
        real = await session.scalar(
            select(Tenant).where(Tenant.clerk_org_id == "org_from_claims")
        )
        assert real is not None
        assert real.id != uuid.UUID(int=0)
