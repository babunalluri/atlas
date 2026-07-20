import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import TenantUserCreateIn, TenantUserOut, TenantUserUpdateIn
from app.auth.dependencies import require_roles
from app.db.models import Membership, Role
from app.db.repositories import MembershipRepository, WorkflowRepository
from app.db.session import tenant_session
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/users", tags=["admin-users"])
AdminContext = Annotated[
    TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
]
TenantSession = Annotated[AsyncSession, Depends(tenant_session)]


async def _user_out(
    membership: Membership,
    workflows: WorkflowRepository,
) -> TenantUserOut:
    return TenantUserOut(
        id=membership.id,
        user_id=membership.user_id,
        display_name=membership.display_name or membership.user_id,
        email=membership.email,
        role=membership.role.value,
        is_active=membership.is_active,
        workflow_ids=await workflows.assigned_workflow_ids(membership.user_id),
        created_at=membership.created_at,
        updated_at=membership.updated_at,
    )


@router.get("", response_model=list[TenantUserOut])
async def list_users(context: AdminContext, session: TenantSession) -> list[TenantUserOut]:
    users = MembershipRepository(session, context)
    workflows = WorkflowRepository(session, context)
    return [await _user_out(row, workflows) for row in await users.list_users()]


@router.post("", response_model=TenantUserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: TenantUserCreateIn,
    context: AdminContext,
    session: TenantSession,
) -> TenantUserOut:
    users = MembershipRepository(session, context)
    workflows = WorkflowRepository(session, context)
    try:
        membership = await users.create(
            user_id=body.user_id,
            display_name=body.display_name,
            email=body.email,
            role=Role(body.role),
            is_active=body.is_active,
        )
        if body.workflow_ids:
            await workflows.replace_user_assignments(membership.user_id, body.workflow_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await _user_out(membership, workflows)


@router.get("/{membership_id}", response_model=TenantUserOut)
async def get_user(
    membership_id: uuid.UUID,
    context: AdminContext,
    session: TenantSession,
) -> TenantUserOut:
    users = MembershipRepository(session, context)
    workflows = WorkflowRepository(session, context)
    membership = await users.get(membership_id)
    if membership is None:
        raise HTTPException(status_code=404, detail="User not found")
    return await _user_out(membership, workflows)


@router.patch("/{membership_id}", response_model=TenantUserOut)
async def update_user(
    membership_id: uuid.UUID,
    body: TenantUserUpdateIn,
    context: AdminContext,
    session: TenantSession,
) -> TenantUserOut:
    users = MembershipRepository(session, context)
    workflows = WorkflowRepository(session, context)
    try:
        membership = await users.update(
            membership_id,
            display_name=body.display_name,
            email=body.email,
            role=Role(body.role) if body.role else None,
            is_active=body.is_active,
        )
        if membership is None:
            raise HTTPException(status_code=404, detail="User not found")
        if body.workflow_ids is not None:
            await workflows.replace_user_assignments(membership.user_id, body.workflow_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await _user_out(membership, workflows)


@router.delete("/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    membership_id: uuid.UUID,
    context: AdminContext,
    session: TenantSession,
) -> Response:
    users = MembershipRepository(session, context)
    deleted = await users.delete(membership_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail="User not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
