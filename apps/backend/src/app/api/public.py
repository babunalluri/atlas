from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import AgentConfigOut, AgentVersionOut, TenantOut
from app.auth.dependencies import require_tenant
from app.db.models import Role
from app.db.repositories import (
    AgentRepository,
    TeamRepository,
    TenantAdminRepository,
    WorkflowRepository,
)
from app.db.session import SessionFactory, tenant_session
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/public", tags=["public"])


async def _public_tenant_context(tenant_slug: str):  # type: ignore[no-untyped-def]
    session = SessionFactory()
    tenant = await TenantAdminRepository(session).get_by_slug(tenant_slug)
    if tenant is None or not tenant.is_active:
        await session.close()
        raise HTTPException(status_code=404, detail="Tenant not found")
    if session.bind and session.bind.dialect.name == "postgresql":
        from sqlalchemy import text

        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant.id)},
        )
    session.info["tenant_id"] = tenant.id
    context = TenantContext(
        tenant_id=tenant.id,
        user_id="public-surface",
        role=Role.end_user,
        auth_org_id=tenant.auth_org_id,
    )
    return session, tenant, context


_PUBLIC_AGENT_DISABLED = (
    "Public agent chat is not available. Use a published team or workflow."
)


@router.get("/t/{tenant_slug}/agents/{agent_slug}")
async def get_chat_surface(tenant_slug: str, agent_slug: str) -> dict[str, object]:
    """Agents are not publicly callable — use team or workflow surfaces."""
    del tenant_slug, agent_slug
    raise HTTPException(status_code=404, detail=_PUBLIC_AGENT_DISABLED)


@router.get("/t/{tenant_slug}/teams/{team_slug}")
async def get_team_chat_surface(tenant_slug: str, team_slug: str) -> dict[str, object]:
    """Return non-sensitive branding and published team metadata."""
    session, tenant, context = await _public_tenant_context(tenant_slug)
    try:
        config = await TeamRepository(session, context).get_config_by_slug(team_slug)
        if config is None or config.published_version_id is None:
            raise HTTPException(status_code=404, detail="Published team not found")
        branding = tenant.branding or {}
        return {
            "tenant": {
                "name": tenant.name,
                "slug": tenant.slug,
                "primaryColor": branding.get("primaryColor", "#0f766e"),
                "accentColor": branding.get("accentColor", "#5eead4"),
                "logoUrl": branding.get("logoUrl"),
                "tagline": branding.get("tagline"),
            },
            "team": {
                "id": str(config.id),
                "name": config.name,
                "slug": config.slug,
                "description": config.description or "",
                "welcomeMessage": branding.get(
                    "teamWelcomeMessage",
                    f"Hi, we're {config.name}. How can our specialists help?",
                ),
            },
        }
    finally:
        await session.close()


@router.get("/t/{tenant_slug}/workflows/{workflow_slug}")
async def get_workflow_surface(tenant_slug: str, workflow_slug: str) -> dict[str, object]:
    """Return non-sensitive branding and published workflow metadata."""
    session, tenant, context = await _public_tenant_context(tenant_slug)
    try:
        repo = WorkflowRepository(session, context)
        config = await repo.get_config_by_slug(workflow_slug)
        if config is None or config.published_version_id is None:
            raise HTTPException(status_code=404, detail="Published workflow not found")
        branding = tenant.branding or {}
        team_repo = TeamRepository(session, context)
        teams: list[dict[str, object]] = []
        for step in await repo.steps(config.published_version_id):
            if step.target_type != "team" or step.team_config_id is None:
                continue
            team = await team_repo.get_config(step.team_config_id)
            if team is None:
                continue
            teams.append(
                {
                    "id": str(team.id),
                    "name": team.name,
                    "slug": team.slug,
                    "stepName": step.name,
                }
            )
        return {
            "tenant": {
                "name": tenant.name,
                "slug": tenant.slug,
                "primaryColor": branding.get("primaryColor", "#0f766e"),
                "accentColor": branding.get("accentColor", "#5eead4"),
                "logoUrl": branding.get("logoUrl"),
                "tagline": branding.get("tagline"),
            },
            "workflow": {
                "id": str(config.id),
                "name": config.name,
                "slug": config.slug,
                "description": config.description or "",
                "welcomeMessage": branding.get(
                    "workflowWelcomeMessage",
                    f"Hi, I'm the {config.name} workflow. What should we work on?",
                ),
                "teams": teams,
            },
        }
    finally:
        await session.close()


@router.get("/tenants/{slug}", response_model=TenantOut)
async def get_tenant_branding(slug: str) -> TenantOut:
    async with SessionFactory() as session:
        tenant = await TenantAdminRepository(session).get_by_slug(slug)
        if tenant is None or not tenant.is_active:
            raise HTTPException(status_code=404, detail="Tenant not found")
        return TenantOut(
            id=tenant.id, slug=tenant.slug, name=tenant.name, branding=tenant.branding or {}
        )


@router.get("/agents/{slug}", response_model=AgentConfigOut)
async def get_published_agent(
    slug: str,
    context: Annotated[TenantContext, Depends(require_tenant)],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> AgentConfigOut:
    repo = AgentRepository(session, context)
    config = await repo.get_config_by_slug(slug)
    if config is None or config.published_version_id is None:
        raise HTTPException(status_code=404, detail="Published agent not found")
    published = await repo.get_version(config.published_version_id, allow_draft=False)
    if published is None:
        raise HTTPException(status_code=404, detail="Published agent not found")
    return AgentConfigOut(
        id=config.id,
        slug=config.slug,
        name=config.name,
        description=config.description,
        published_version_id=config.published_version_id,
        updated_at=config.updated_at,
        published=AgentVersionOut(
            id=published.id,
            version=published.version,
            status=published.status.value,
            instructions=published.instructions,
            model_id=published.model_id,
            temperature=published.temperature,
            memory_mode=published.memory_mode,
            created_at=published.created_at,
        ),
    )
