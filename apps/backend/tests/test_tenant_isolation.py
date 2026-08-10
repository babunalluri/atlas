import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import AgentConfig, Base, Role, Tenant
from app.db.repositories import AgentRepository
from app.tenancy.context import TenantContext


@pytest_asyncio.fixture
async def session_factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_repository_never_returns_another_tenants_agents(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant_a = Tenant(auth_org_id="org_a", slug="a", name="A")
    tenant_b = Tenant(auth_org_id="org_b", slug="b", name="B")
    async with session_factory() as setup, setup.begin():
        setup.add_all([tenant_a, tenant_b])
        await setup.flush()
        setup.add_all(
            [
                AgentConfig(tenant_id=tenant_a.id, slug="agent-a", name="A agent"),
                AgentConfig(tenant_id=tenant_b.id, slug="agent-b", name="B agent"),
            ]
        )

    context = TenantContext(tenant_a.id, "user_a", Role.tenant_admin, "org_a")
    async with session_factory() as session:
        session.info["tenant_id"] = tenant_a.id
        rows = await AgentRepository(session, context).list_configs()

    assert [row.slug for row in rows] == ["agent-a"]


@pytest.mark.asyncio
async def test_repository_rejects_context_session_mismatch(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant_a_id = uuid.uuid4()
    context = TenantContext(tenant_a_id, "user_a", Role.tenant_admin, "org_a")
    async with session_factory() as session:
        session.info["tenant_id"] = uuid.uuid4()
        with pytest.raises(RuntimeError, match="does not match"):
            AgentRepository(session, context)
