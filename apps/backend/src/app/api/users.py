import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import TenantUserCreateIn, TenantUserOut, TenantUserUpdateIn
from app.auth.dependencies import require_roles
from app.auth.identity_admin import (
    IdentityAdminClient,
    IdentityProvisionError,
    is_pending_user_id,
)
from app.core.settings import get_settings
from app.db.models import Membership, Role
from app.db.repositories import MembershipRepository, TeamRepository, WorkflowRepository
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
    teams: TeamRepository,
    *,
    temporary_password: str | None = None,
    sign_in_url: str | None = None,
) -> TenantUserOut:
    return TenantUserOut(
        id=membership.id,
        user_id=membership.user_id,
        display_name=membership.display_name or membership.user_id,
        email=membership.email,
        phone=membership.phone,
        role=membership.role.value,
        is_active=membership.is_active,
        invite_pending=is_pending_user_id(membership.user_id),
        temporary_password=temporary_password,
        sign_in_url=sign_in_url,
        workflow_ids=await workflows.assigned_workflow_ids(membership.user_id),
        team_ids=await teams.assigned_team_ids(membership.user_id),
        created_at=membership.created_at,
        updated_at=membership.updated_at,
    )


async def _provision_or_raise(
    *,
    context: TenantContext,
    email: str,
    display_name: str,
    role: Role,
):
    from app.auth.identity_admin import ProvisionedIdentity

    settings = get_settings()
    if settings.auth_disabled:
        raise HTTPException(
            status_code=503,
            detail="Cannot sync users while AUTH_DISABLED=true",
        )
    client = IdentityAdminClient(settings)
    try:
        provisioned: ProvisionedIdentity = await client.provision_tenant_user(
            email=email,
            display_name=display_name,
            role=role,
            organization_id=context.auth_org_id,
            inviter_user_id=context.user_id,
            redirect_url=f"{settings.app_public_url.rstrip('/')}/sign-in",
        )
    except IdentityProvisionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return provisioned


@router.get("", response_model=list[TenantUserOut])
async def list_users(context: AdminContext, session: TenantSession) -> list[TenantUserOut]:
    users = MembershipRepository(session, context)
    workflows = WorkflowRepository(session, context)
    teams = TeamRepository(session, context)
    return [
        await _user_out(row, workflows, teams) for row in await users.list_users()
    ]


@router.post("", response_model=TenantUserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: TenantUserCreateIn,
    context: AdminContext,
    session: TenantSession,
) -> TenantUserOut:
    users = MembershipRepository(session, context)
    workflows = WorkflowRepository(session, context)
    teams = TeamRepository(session, context)

    existing_email = await users.get_by_email(body.email)
    if existing_email is not None:
        raise HTTPException(
            status_code=409, detail="A user with this email already exists in the tenant"
        )

    provisioned = await _provision_or_raise(
        context=context,
        email=body.email,
        display_name=body.display_name,
        role=Role(body.role),
    )

    try:
        membership = await users.create(
            user_id=provisioned.user_id,
            display_name=body.display_name,
            email=body.email,
            phone=body.phone,
            role=Role(body.role),
            is_active=body.is_active,
        )
        if body.workflow_ids:
            await workflows.replace_user_assignments(membership.user_id, body.workflow_ids)
        if body.team_ids:
            await teams.replace_user_assignments(membership.user_id, body.team_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await _user_out(
        membership,
        workflows,
        teams,
        temporary_password=provisioned.temporary_password,
        sign_in_url=provisioned.sign_in_url,
    )


@router.post("/{membership_id}/sync-identity", response_model=TenantUserOut)
async def sync_user_identity(
    membership_id: uuid.UUID,
    context: AdminContext,
    session: TenantSession,
) -> TenantUserOut:
    """Create/link the sign-in account for an existing Atlas membership."""
    users = MembershipRepository(session, context)
    workflows = WorkflowRepository(session, context)
    teams = TeamRepository(session, context)
    membership = await users.get(membership_id)
    if membership is None:
        raise HTTPException(status_code=404, detail="User not found")
    if not membership.email:
        raise HTTPException(status_code=400, detail="Email is required to sync identity")

    provisioned = await _provision_or_raise(
        context=context,
        email=membership.email,
        display_name=membership.display_name or membership.email,
        role=membership.role,
    )
    try:
        membership = await users.rebind_user_id(
            membership_id, new_user_id=provisioned.user_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if membership is None:
        raise HTTPException(status_code=404, detail="User not found")
    return await _user_out(
        membership,
        workflows,
        teams,
        temporary_password=provisioned.temporary_password,
        sign_in_url=provisioned.sign_in_url,
    )


@router.get("/{membership_id}", response_model=TenantUserOut)
async def get_user(
    membership_id: uuid.UUID,
    context: AdminContext,
    session: TenantSession,
) -> TenantUserOut:
    users = MembershipRepository(session, context)
    workflows = WorkflowRepository(session, context)
    teams = TeamRepository(session, context)
    membership = await users.get(membership_id)
    if membership is None:
        raise HTTPException(status_code=404, detail="User not found")
    return await _user_out(membership, workflows, teams)


@router.patch("/{membership_id}", response_model=TenantUserOut)
async def update_user(
    membership_id: uuid.UUID,
    body: TenantUserUpdateIn,
    context: AdminContext,
    session: TenantSession,
) -> TenantUserOut:
    users = MembershipRepository(session, context)
    workflows = WorkflowRepository(session, context)
    teams = TeamRepository(session, context)
    try:
        if body.email is not None:
            other = await users.get_by_email(body.email)
            if other is not None and other.id != membership_id:
                raise HTTPException(
                    status_code=409,
                    detail="A user with this email already exists in the tenant",
                )
        dump = body.model_dump(exclude_unset=True)
        membership = await users.update(
            membership_id,
            display_name=body.display_name,
            email=body.email,
            phone=dump.get("phone") if dump.get("phone") else None,
            clear_phone="phone" in dump and not dump.get("phone"),
            role=Role(body.role) if body.role else None,
            is_active=body.is_active,
        )
        if membership is None:
            raise HTTPException(status_code=404, detail="User not found")
        if body.workflow_ids is not None:
            await workflows.replace_user_assignments(membership.user_id, body.workflow_ids)
        if body.team_ids is not None:
            await teams.replace_user_assignments(membership.user_id, body.team_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await _user_out(membership, workflows, teams)


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
