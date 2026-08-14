import uuid
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    TenantUserCreateIn,
    TenantUserOut,
    TenantUserPasswordChangeIn,
    TenantUserPasswordIn,
    TenantUserUpdateIn,
)
from app.auth.dependencies import require_roles, require_tenant
from app.auth.identity_admin import (
    IdentityAdminClient,
    IdentityProvisionError,
    humanize_identity_error,
    is_pending_user_id,
)
from app.core.settings import get_settings
from app.db.email_uniqueness import EMAIL_ALREADY_IN_USE, email_taken_across_tenants
from app.db.models import Membership, Role
from app.db.repositories import MembershipRepository, TeamRepository, WorkflowRepository
from app.db.session import tenant_session
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/users", tags=["admin-users"])
AdminContext = Annotated[
    TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
]
AnyUserContext = Annotated[TenantContext, Depends(require_tenant)]
TenantSession = Annotated[AsyncSession, Depends(tenant_session)]


async def _user_out(
    membership: Membership,
    workflows: WorkflowRepository,
    teams: TeamRepository,
    *,
    temporary_password: str | None = None,
    sign_in_url: str | None = None,
) -> TenantUserOut:
    user_id = membership.user_id
    snapshot = TenantUserOut(
        id=membership.id,
        user_id=user_id,
        display_name=membership.display_name or user_id,
        email=membership.email,
        phone=membership.phone,
        role=membership.role.value,
        is_active=membership.is_active,
        timezone=membership.timezone or "UTC",
        invite_pending=is_pending_user_id(user_id),
        temporary_password=temporary_password,
        sign_in_url=sign_in_url,
        created_at=membership.created_at,
        updated_at=membership.updated_at,
    )
    return snapshot.model_copy(
        update={
            "workflow_ids": await workflows.assigned_workflow_ids(user_id),
            "team_ids": await teams.assigned_team_ids(user_id),
        }
    )


def _raise_identity(exc: IdentityProvisionError) -> NoReturn:
    raise HTTPException(
        status_code=exc.status_code,
        detail=humanize_identity_error(str(exc)),
    ) from exc


async def _resolve_identity_profile(client: IdentityAdminClient, membership: Membership):
    profile = await client.get_user(membership.user_id)
    if profile is None and membership.email:
        profile = await client.find_user_by_email(membership.email)
    return profile


async def _sync_identity_update(
    *,
    membership: Membership,
    display_name: str | None,
    email: str | None,
    role: Role | None,
    password: str | None,
) -> None:
    identity_fields = any(
        value is not None for value in (display_name, email, role, password)
    )
    if not identity_fields:
        return
    if is_pending_user_id(membership.user_id):
        if password:
            raise HTTPException(
                status_code=400,
                detail="This user does not have a sign-in account yet",
            )
        return
    if membership.role == Role.platform_admin:
        raise HTTPException(
            status_code=403,
            detail="Cannot edit or reset a platform administrator",
        )

    settings = get_settings()
    client = IdentityAdminClient(settings)
    if not client.configured():
        if password:
            raise HTTPException(
                status_code=503,
                detail="Keycloak admin is not configured",
            )
        return
    if membership.user_id.startswith("local:"):
        if password:
            raise HTTPException(
                status_code=400,
                detail="This user does not have a sign-in account yet",
            )
        return
    try:
        profile = await _resolve_identity_profile(client, membership)
        if profile is None:
            raise HTTPException(status_code=404, detail="Identity user not found")
        current_email = (membership.email or "").strip().lower()
        identity_email = email
        if identity_email is not None and identity_email.strip().lower() == current_email:
            identity_email = None
        await client.update_org_user(
            str(profile.get("id") or membership.user_id),
            email=identity_email,
            display_name=display_name,
            role=role,
            password=password,
        )
    except IdentityProvisionError as exc:
        _raise_identity(exc)


async def _provision_or_raise(
    *,
    context: TenantContext,
    email: str,
    display_name: str,
    role: Role,
    password: str | None = None,
):
    from app.auth.identity_admin import ProvisionedIdentity

    settings = get_settings()
    client = IdentityAdminClient(settings)
    if client.configured():
        try:
            return await client.provision_tenant_user(
                email=email,
                display_name=display_name,
                role=role,
                organization_id=context.auth_org_id,
                inviter_user_id=context.user_id,
                redirect_url=f"{settings.app_public_url.rstrip('/')}/sign-in",
                password=password,
            )
        except IdentityProvisionError as exc:
            _raise_identity(exc)
    if settings.identity_invite_enabled and not password:
        try:
            return await client.provision_pending_invite(
                email=email,
                display_name=display_name,
                role=role,
                organization_id=context.auth_org_id,
                inviter_user_id=context.user_id,
                redirect_url=f"{settings.app_public_url.rstrip('/')}/sign-in",
            )
        except IdentityProvisionError as exc:
            _raise_identity(exc)
    if settings.auth_disabled and password:
        normalized = email.strip().lower()
        return ProvisionedIdentity(
            user_id=f"local:{normalized}",
            email=normalized,
            invite_pending=False,
        )
    if not password:
        raise HTTPException(status_code=400, detail="Password is required")
    raise HTTPException(
        status_code=503,
        detail="Keycloak admin is not configured",
    )


@router.get("", response_model=list[TenantUserOut])
async def list_users(context: AdminContext, session: TenantSession) -> list[TenantUserOut]:
    users = MembershipRepository(session, context)
    workflows = WorkflowRepository(session, context)
    teams = TeamRepository(session, context)
    return [
        await _user_out(row, workflows, teams) for row in await users.list_users()
    ]


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_my_password(
    body: TenantUserPasswordChangeIn,
    context: AnyUserContext,
) -> Response:
    settings = get_settings()
    client = IdentityAdminClient(settings)
    if not client.configured():
        raise HTTPException(
            status_code=503,
            detail="Keycloak admin is not configured",
        )
    try:
        await client.change_password(
            user_id=context.user_id,
            username=context.user_id,
            current_password=body.current_password.get_secret_value(),
            new_password=body.new_password.get_secret_value(),
        )
    except IdentityProvisionError as exc:
        _raise_identity(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("", response_model=TenantUserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: TenantUserCreateIn,
    context: AdminContext,
    session: TenantSession,
) -> TenantUserOut:
    users = MembershipRepository(session, context)
    workflows = WorkflowRepository(session, context)
    teams = TeamRepository(session, context)

    if await email_taken_across_tenants(session, body.email):
        raise HTTPException(status_code=409, detail=EMAIL_ALREADY_IN_USE)

    password = body.password.get_secret_value() if body.password is not None else None
    provisioned = await _provision_or_raise(
        context=context,
        email=body.email,
        display_name=body.display_name,
        role=Role(body.role),
        password=password,
    )
    rollback_identity = (
        not provisioned.invite_pending and not provisioned.user_id.startswith("local:")
    )

    try:
        membership = await users.create(
            user_id=provisioned.user_id,
            display_name=body.display_name,
            email=body.email,
            phone=body.phone,
            role=Role(body.role),
            is_active=body.is_active,
            timezone=body.timezone,
        )
        if body.workflow_ids:
            await workflows.replace_user_assignments(membership.user_id, body.workflow_ids)
        if body.team_ids:
            await teams.replace_user_assignments(membership.user_id, body.team_ids)
        # Starter desk teams are a create-time default, not a lock on later edits.
        from app.domains.access import assign_domain_default_teams

        await assign_domain_default_teams(session, context, membership.user_id)
    except Exception as exc:
        if rollback_identity:
            settings = get_settings()
            client = IdentityAdminClient(settings)
            if client.configured():
                await client.delete_user(provisioned.user_id)
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if isinstance(exc, LookupError):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise
    return await _user_out(
        membership,
        workflows,
        teams,
        temporary_password=None,
        sign_in_url=provisioned.sign_in_url,
    )


@router.post("/{membership_id}/password", status_code=status.HTTP_204_NO_CONTENT)
async def set_user_password(
    membership_id: uuid.UUID,
    body: TenantUserPasswordIn,
    context: AdminContext,
    session: TenantSession,
) -> Response:
    users = MembershipRepository(session, context)
    membership = await users.get(membership_id)
    if membership is None:
        raise HTTPException(status_code=404, detail="User not found")
    if is_pending_user_id(membership.user_id):
        raise HTTPException(
            status_code=400,
            detail="This user does not have a sign-in account yet",
        )
    if membership.role == Role.platform_admin:
        raise HTTPException(
            status_code=403,
            detail="Cannot edit or reset a platform administrator",
        )
    settings = get_settings()
    client = IdentityAdminClient(settings)
    if not client.configured():
        raise HTTPException(
            status_code=503,
            detail="Keycloak admin is not configured",
        )
    try:
        profile = await _resolve_identity_profile(client, membership)
        if profile is None:
            raise HTTPException(status_code=404, detail="Identity user not found")
        await client.set_password(
            str(profile.get("id") or membership.user_id),
            body.password.get_secret_value(),
        )
    except IdentityProvisionError as exc:
        _raise_identity(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{membership_id}/sync-identity", response_model=TenantUserOut)
async def sync_user_identity(
    membership_id: uuid.UUID,
    context: AdminContext,
    session: TenantSession,
) -> TenantUserOut:
    """Link an existing Keycloak user, or keep pending invite when enabled."""
    users = MembershipRepository(session, context)
    workflows = WorkflowRepository(session, context)
    teams = TeamRepository(session, context)
    membership = await users.get(membership_id)
    if membership is None:
        raise HTTPException(status_code=404, detail="User not found")
    if not membership.email:
        raise HTTPException(status_code=400, detail="Email is required to sync identity")

    settings = get_settings()
    client = IdentityAdminClient(settings)
    if client.configured():
        try:
            profile = await client.find_user_by_email(membership.email)
        except IdentityProvisionError as exc:
            _raise_identity(exc)
            raise
        if profile is None or not profile.get("id"):
            raise HTTPException(
                status_code=400,
                detail="No Keycloak user exists for this email — create the user with a password",
            )
        provisioned_user_id = str(profile["id"])
    else:
        provisioned = await _provision_or_raise(
            context=context,
            email=membership.email,
            display_name=membership.display_name or membership.email,
            role=membership.role,
        )
        provisioned_user_id = provisioned.user_id
    try:
        membership = await users.rebind_user_id(
            membership_id, new_user_id=provisioned_user_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if membership is None:
        raise HTTPException(status_code=404, detail="User not found")
    return await _user_out(membership, workflows, teams)


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
    membership = await users.get(membership_id)
    if membership is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        if body.email is not None:
            current_email = (membership.email or "").strip().lower()
            if body.email != current_email and await email_taken_across_tenants(
                session, body.email, exclude_membership_id=membership_id
            ):
                raise HTTPException(status_code=409, detail=EMAIL_ALREADY_IN_USE)
        dump = body.model_dump(exclude_unset=True)
        password = (
            body.password.get_secret_value() if body.password is not None else None
        )
        await _sync_identity_update(
            membership=membership,
            display_name=body.display_name,
            email=body.email,
            role=Role(body.role) if body.role else None,
            password=password,
        )
        membership = await users.update(
            membership_id,
            display_name=body.display_name,
            email=body.email,
            phone=dump.get("phone") if dump.get("phone") else None,
            clear_phone="phone" in dump and not dump.get("phone"),
            role=Role(body.role) if body.role else None,
            is_active=body.is_active,
            timezone=body.timezone,
        )
        if membership is None:
            raise HTTPException(status_code=404, detail="User not found")
        if body.workflow_ids is not None:
            await workflows.replace_user_assignments(membership.user_id, body.workflow_ids)
        if body.team_ids is not None:
            await teams.replace_user_assignments(membership.user_id, body.team_ids)
        await session.refresh(membership)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await _user_out(membership, workflows, teams)


_PROTECTED_DELETE_ROLES = {Role.tenant_admin, Role.platform_admin}


@router.delete("/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    membership_id: uuid.UUID,
    context: AdminContext,
    session: TenantSession,
) -> Response:
    users = MembershipRepository(session, context)
    membership = await users.get(membership_id)
    if membership is None:
        raise HTTPException(status_code=404, detail="User not found")
    if membership.role in _PROTECTED_DELETE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin users cannot be deleted",
        )
    deleted = await users.delete(membership_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail="User not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
