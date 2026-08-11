"""Current-tenant workspace metadata for admin UI (embed URLs, etc.)."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_tenant
from app.db.models import Role, Tenant
from app.db.repositories import MembershipRepository
from app.db.session import tenant_session
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/workspace", tags=["workspace"])


class WorkspaceInfoOut(BaseModel):
    id: UUID
    name: str
    slug: str
    branding: dict[str, Any]
    email_inbound_domain: str | None = None
    user_id: str
    role: Role
    can_administer: bool
    timezone: str = "UTC"
    tenant_timezone: str = "UTC"


@router.get("", response_model=WorkspaceInfoOut)
async def get_workspace(
    context: Annotated[TenantContext, Depends(require_tenant)],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> WorkspaceInfoOut:
    tenant = await session.scalar(select(Tenant).where(Tenant.id == context.tenant_id))
    if tenant is None:
        # Should not happen when require_tenant succeeded, but keep a clear error.
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Workspace not found")
    from app.core.settings import get_settings

    domain = get_settings().email_inbound_domain.strip() or None
    tenant_tz = getattr(tenant, "timezone", None) or "UTC"
    membership = await MembershipRepository(session, context).get_by_user_id(
        context.user_id
    )
    user_tz = (
        membership.timezone
        if membership is not None and membership.timezone
        else tenant_tz
    )
    return WorkspaceInfoOut(
        id=tenant.id,
        name=tenant.name,
        slug=tenant.slug,
        branding=tenant.branding or {},
        email_inbound_domain=domain,
        user_id=context.user_id,
        role=context.role,
        can_administer=context.can_administer(),
        timezone=user_tz,
        tenant_timezone=tenant_tz,
    )
