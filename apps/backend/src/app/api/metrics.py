from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.db.models import Role
from app.db.session import tenant_session
from app.metrics.service import MetricsService
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/api/admin/metrics", tags=["admin-metrics"])
AdminContext = Annotated[
    TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
]
TenantSession = Annotated[AsyncSession, Depends(tenant_session)]


@router.get("")
async def get_metrics(
    context: AdminContext,
    session: TenantSession,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> dict[str, Any]:
    return await MetricsService(session, context).dashboard(days=days)


@router.post("/refresh")
async def refresh_metrics(
    context: AdminContext,
    session: TenantSession,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> dict[str, Any]:
    return await MetricsService(session, context).dashboard(days=days)
