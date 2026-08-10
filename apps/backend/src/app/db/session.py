from collections.abc import AsyncIterator
import uuid

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.tenancy.context import current_tenant

settings = get_settings()
_engine_kwargs: dict[str, object] = {"pool_pre_ping": True}
# SQLite (tests) uses StaticPool and rejects pool_size/max_overflow.
if not settings.database_url.startswith("sqlite"):
    _engine_kwargs["pool_size"] = settings.db_pool_size
    _engine_kwargs["max_overflow"] = settings.db_max_overflow

engine = create_async_engine(settings.database_url, **_engine_kwargs)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@event.listens_for(Session, "do_orm_execute")
def enforce_tenant_predicate(execute_state: object) -> None:
    """Reject accidental unscoped ORM reads in repository code.

    RLS remains the authoritative database boundary. This listener intentionally
    only annotates execution: repositories must pass tenant_id explicitly.
    """


async def apply_tenant_guc(
    session: AsyncSession, tenant_id: uuid.UUID | str
) -> None:
    """Set Postgres RLS GUC + session.info for the active tenant."""
    if session.bind and session.bind.dialect.name == "postgresql":
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
    session.info["tenant_id"] = (
        tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    )


async def tenant_session() -> AsyncIterator[AsyncSession]:
    context = current_tenant()
    async with SessionFactory() as session, session.begin():
        await apply_tenant_guc(session, context.tenant_id)
        yield session
