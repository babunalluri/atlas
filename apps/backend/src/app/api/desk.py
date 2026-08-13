"""Customer-facing domain desk (end users and admins)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_tenant
from app.db.session import tenant_session
from app.domains.dashboard import DomainDashboardService
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/api/desk", tags=["desk"])
TenantSession = Annotated[AsyncSession, Depends(tenant_session)]
AuthContext = Annotated[TenantContext, Depends(require_tenant)]


@router.get("")
async def get_customer_desk(
    context: AuthContext,
    session: TenantSession,
    desk_snapshot: bool = Query(False),
) -> dict[str, Any]:
    return await DomainDashboardService(session, context).customer_desk(
        desk_snapshot=desk_snapshot
    )
