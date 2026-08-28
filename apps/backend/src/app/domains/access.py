"""Assign domain starter teams so end users can run the desk without a manual Users step."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Membership, Role, Tenant
from app.db.repositories import TeamRepository
from app.domains.types import (
    DOMAIN_DEFAULT_TEAM_SLUGS,
    STOCK_BROKER_ADMIN_DESK_TEAMS,
    normalize_domain,
)
from app.tenancy.context import TenantContext


async def _target_gets_admin_ops(
    session: AsyncSession,
    context: TenantContext,
    user_id: str,
    admin_ops: bool | None,
) -> bool:
    if admin_ops is not None:
        return admin_ops
    membership = await session.scalar(
        select(Membership).where(
            Membership.tenant_id == context.tenant_id,
            Membership.user_id == user_id,
        )
    )
    if membership is not None:
        return membership.role in {Role.tenant_admin, Role.platform_admin}
    # Self-serve provision assigns to the actor before a membership row exists.
    return user_id == context.user_id and context.can_administer()


async def assign_domain_default_teams(
    session: AsyncSession,
    context: TenantContext,
    user_id: str,
    *,
    admin_ops: bool | None = None,
) -> list[str]:
    """Assign published default desk teams on create/provision.

    Tenant admins also receive signals-ops (Signal chat). Customers get the
    four desk teams only. Callers must not use this on user update or desk
    load — admins can unassign starter teams, and that choice should persist.
    """
    if not user_id.strip():
        return []
    tenant = await session.scalar(select(Tenant).where(Tenant.id == context.tenant_id))
    domain = normalize_domain(getattr(tenant, "domain", None) if tenant else None)
    slugs = DOMAIN_DEFAULT_TEAM_SLUGS.get(domain, ())
    if domain == "stock_broker" and await _target_gets_admin_ops(
        session, context, user_id, admin_ops
    ):
        slugs = STOCK_BROKER_ADMIN_DESK_TEAMS
    if not slugs:
        return []
    teams = TeamRepository(session, context)
    team_ids = []
    for slug in slugs:
        config = await teams.get_config_by_slug(slug)
        if config is None or config.published_version_id is None:
            continue
        team_ids.append(config.id)
    if not team_ids:
        return []
    assigned = await teams.ensure_user_assignments(user_id, team_ids)
    return [str(value) for value in assigned]
