"""Realm-wide staff email uniqueness across Atlas memberships."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.models import Membership, Tenant
from app.db.session import apply_tenant_guc

EMAIL_ALREADY_IN_USE = "This email is already in use."


def normalize_membership_email(email: str | None) -> str | None:
    cleaned = (email or "").strip().lower()
    return cleaned or None


async def email_taken_across_tenants(
    session: AsyncSession,
    email: str,
    *,
    exclude_membership_id: uuid.UUID | None = None,
) -> bool:
    """True when any tenant already has this membership email.

    Tenant-scoped RLS hides other orgs on Postgres, so that path walks tenants
    with a restored GUC. SQLite tests have no RLS and search all rows at once.
    """
    normalized = normalize_membership_email(email)
    if not normalized or "@" not in normalized:
        return False

    def statement():
        stmt = select(Membership.id).where(func.lower(Membership.email) == normalized)
        if exclude_membership_id is not None:
            stmt = stmt.where(Membership.id != exclude_membership_id)
        return stmt.limit(1)

    dialect = ""
    bind = session.bind
    if bind is None:
        bind = session.get_bind()
    if bind is not None:
        dialect = bind.dialect.name
    elif get_settings().database_url.startswith("postgresql"):
        dialect = "postgresql"
    if dialect != "postgresql":
        return await session.scalar(statement()) is not None

    current = session.info.get("tenant_id")
    tenant_ids = list(await session.scalars(select(Tenant.id)))
    try:
        for tenant_id in tenant_ids:
            await apply_tenant_guc(session, tenant_id)
            if await session.scalar(statement()) is not None:
                return True
        return False
    finally:
        if current is not None:
            await apply_tenant_guc(session, current)
        else:
            await session.execute(text("SELECT set_config('app.tenant_id', '', true)"))
            session.info.pop("tenant_id", None)
