import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("AUTH_DISABLED", "true")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "dev-only-change-me-please-32b")
os.environ.setdefault("REDIS_URL", "memory://")
os.environ.setdefault("DOCUMENT_BUCKET", "")

from app.core.settings import get_settings
from app.db.models import Base, Role, Tenant
from app.main import app
from app.tenancy.context import TenantContext

get_settings.cache_clear()


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        tenant = Tenant(
            id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
            clerk_org_id="org_demo_acme",
            slug="acme",
            name="Acme Corp",
            branding={"primaryColor": "#0f766e"},
        )
        other = Tenant(
            id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
            clerk_org_id="org_demo_globex",
            slug="globex",
            name="Globex Inc",
            branding={},
        )
        session.add_all([tenant, other])
        await session.commit()
        yield session


@pytest.fixture
def tenant_a() -> TenantContext:
    return TenantContext(
        tenant_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        user_id="user-a",
        role=Role.tenant_admin,
        clerk_org_id="org_demo_acme",
    )


@pytest.fixture
def tenant_b() -> TenantContext:
    return TenantContext(
        tenant_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        user_id="user-b",
        role=Role.tenant_admin,
        clerk_org_id="org_demo_globex",
    )


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
