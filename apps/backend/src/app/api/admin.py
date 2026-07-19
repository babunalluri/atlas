import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.db.models import AgentConfig, Role
from app.db.repositories import AgentRepository, ApprovalRepository
from app.db.session import tenant_session
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/api/admin", tags=["admin"])
AdminContext = Annotated[
    TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
]
TenantSession = Annotated[AsyncSession, Depends(tenant_session)]


class AgentCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)


class DraftCreate(BaseModel):
    instructions: str = Field(min_length=1, max_length=100_000)
    model_id: str
    temperature: float = Field(default=0.2, ge=0, le=2)


class ApprovalDecision(BaseModel):
    approved: bool


@router.get("/agents")
async def list_agents(context: AdminContext, session: TenantSession) -> list[dict[str, object]]:
    rows = await AgentRepository(session, context).list_configs()
    return [
        {
            "id": row.id,
            "slug": row.slug,
            "name": row.name,
            "description": row.description,
            "published_version_id": row.published_version_id,
        }
        for row in rows
    ]


@router.post("/agents", status_code=status.HTTP_201_CREATED)
async def create_agent(
    payload: AgentCreate, context: AdminContext, session: TenantSession
) -> dict[str, object]:
    config = AgentConfig(
        tenant_id=context.tenant_id,
        slug=payload.slug,
        name=payload.name,
        description=payload.description,
    )
    session.add(config)
    await session.flush()
    return {"id": config.id, "slug": config.slug, "name": config.name}


@router.post("/agents/{agent_id}/versions", status_code=status.HTTP_201_CREATED)
async def create_draft(
    agent_id: uuid.UUID,
    payload: DraftCreate,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, object]:
    try:
        version = await AgentRepository(session, context).create_draft(
            config_id=agent_id,
            instructions=payload.instructions,
            model_id=payload.model_id,
            temperature=payload.temperature,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"id": version.id, "version": version.version, "status": version.status}


@router.post("/approvals/{approval_id}/decision")
async def decide_approval(
    approval_id: uuid.UUID,
    payload: ApprovalDecision,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, object]:
    approval = await ApprovalRepository(session, context).resolve(approval_id, payload.approved)
    if approval is None:
        raise HTTPException(status_code=404, detail="Pending approval not found")
    return {"id": approval.id, "status": approval.status}
