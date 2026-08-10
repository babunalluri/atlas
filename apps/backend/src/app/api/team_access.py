from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_tenant
from app.db.repositories import MembershipRepository, TeamRepository
from app.db.session import tenant_session
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/api/teams", tags=["team-access"])


@router.get("/available")
async def list_available_teams(
    context: Annotated[TenantContext, Depends(require_tenant)],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> list[dict[str, object]]:
    """List published teams the current user may run."""
    membership = await MembershipRepository(session, context).get_by_user_id(
        context.user_id
    )
    if membership is not None and not membership.is_active:
        return []

    repo = TeamRepository(session, context)
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
