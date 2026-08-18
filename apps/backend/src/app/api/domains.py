from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.db.models import Role
from app.db.session import tenant_session
from app.domains.dashboard import DomainDashboardService
from app.domains.types import DOMAIN_LABELS, WORKSPACE_DOMAINS
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/domains", tags=["domains"])
AdminContext = Annotated[
    TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
]
TenantSession = Annotated[AsyncSession, Depends(tenant_session)]


@router.get("/catalog")
async def list_workspace_domains() -> list[dict[str, str]]:
    return [{"id": domain, "label": DOMAIN_LABELS[domain]} for domain in WORKSPACE_DOMAINS]


@router.get("/dashboard")
async def get_domain_dashboard(
    context: AdminContext,
    session: TenantSession,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
    desk_snapshot: bool = Query(False),
) -> dict[str, Any]:
    return await DomainDashboardService(session, context).dashboard(
        days=days, desk_snapshot=desk_snapshot
    )


@router.get("/desk")
async def get_admin_desk(
    context: AdminContext,
    session: TenantSession,
    desk_snapshot: bool = Query(False),
) -> dict[str, Any]:
    return await DomainDashboardService(session, context).admin_desk(
        desk_snapshot=desk_snapshot
    )
