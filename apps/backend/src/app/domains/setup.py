"""Shared tenant bootstrap: domain, branding, and starter pack provisioning."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Role, Tenant
from app.domains.provision import provision_domain_workspace
from app.domains.types import default_branding, normalize_domain
from app.tenancy.context import TenantContext


async def apply_tenant_domain(
    session: AsyncSession,
    *,
    tenant: Tenant,
    actor_user_id: str,
    domain: str | None,
    branding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Set domain/branding on a new tenant and provision its starter workspace."""
    normalized = normalize_domain(domain)
    tenant.domain = normalized
    defaults = default_branding(normalized)
    tenant.branding = {**defaults, **(branding or {})}

    if session.bind and session.bind.dialect.name == "postgresql":
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant.id)},
        )
    session.info["tenant_id"] = tenant.id

    context = TenantContext(
        tenant_id=tenant.id,
        user_id=actor_user_id,
        role=Role.tenant_admin,
        auth_org_id=tenant.auth_org_id,
    )
    return await provision_domain_workspace(session, context=context, domain=normalized)
