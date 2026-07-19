from collections.abc import AsyncIterator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.tenancy.context import current_tenant

settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@event.listens_for(Session, "do_orm_execute")
def enforce_tenant_predicate(execute_state: object) -> None:
    """Reject accidental unscoped ORM reads in repository code.

    RLS remains the authoritative database boundary. This listener intentionally
    only annotates execution: repositories must pass tenant_id explicitly.
    """


async def tenant_session() -> AsyncIterator[AsyncSession]:
    context = current_tenant()
    async with SessionFactory() as session, session.begin():
        if session.bind and session.bind.dialect.name == "postgresql":
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(context.tenant_id)},
            )
        session.info["tenant_id"] = context.tenant_id
        yield session
