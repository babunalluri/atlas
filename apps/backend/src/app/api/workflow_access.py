from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_tenant
from app.db.repositories import WorkflowRepository
from app.db.session import tenant_session
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/api/workflows", tags=["workflow-access"])


@router.get("/available")
async def list_available_workflows(
    context: Annotated[TenantContext, Depends(require_tenant)],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> list[dict[str, object]]:
    """List published workflows the current user may run."""
    repo = WorkflowRepository(session, context)
    configs = (
        [config for config in await repo.list_configs() if config.published_version_id]
        if context.can_administer()
        else await repo.list_available_for_user(context.user_id)
    )
    return [
        {
            "id": str(config.id),
            "name": config.name,
            "slug": config.slug,
            "description": config.description or "",
        }
        for config in configs
    ]
