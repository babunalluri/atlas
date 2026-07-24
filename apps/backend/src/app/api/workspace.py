"""Current-tenant workspace metadata for admin UI (embed URLs, etc.)."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_tenant
from app.db.models import Tenant
from app.db.session import tenant_session
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/workspace", tags=["workspace"])


class WorkspaceInfoOut(BaseModel):
    id: UUID
    name: str
    slug: str
    branding: dict[str, Any]


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
    return WorkspaceInfoOut(
        id=tenant.id,
        name=tenant.name,
        slug=tenant.slug,
        branding=tenant.branding or {},
    )
