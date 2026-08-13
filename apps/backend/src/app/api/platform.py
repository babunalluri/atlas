import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.actor_labels import resolve_membership_profiles
from app.api.schemas import (
    PlatformPythonPackageIn,
    PlatformPythonPackageOut,
    PlatformPythonPackageUpdateIn,
)
from app.auth.dependencies import require_roles
from app.auth.identity_admin import (
    IdentityAdminClient,
    IdentityProvisionError,
    validate_password,
)
from app.billing.service import BillingService
from app.core.settings import get_settings
from app.db.email_uniqueness import EMAIL_ALREADY_IN_USE, email_taken_across_tenants
from app.db.models import Membership, PlatformAuditEvent, Role, Tenant
from app.db.repositories import MembershipRepository, PlatformPythonPackageRepository
from app.db.session import SessionFactory, apply_tenant_guc
from app.domains.setup import apply_tenant_domain
from app.domains.types import normalize_domain
from app.platform.tenant_import import (
    collect_import_bundle,
    list_tenant_catalog,
    materialize_import_bundle,
)
from app.tenancy.context import TenantContext
from app.tenancy.ids import new_id, validate_slug

router = APIRouter(prefix="/admin/platform", tags=["platform-admin"])
PlatformContext = Annotated[
    TenantContext, Depends(require_roles(Role.platform_admin))
]


async def platform_session() -> AsyncIterator[AsyncSession]:
    """Session for non-tenant tables; access is guarded by PlatformContext."""

    async with SessionFactory() as session, session.begin():
        yield session


PlatformSession = Annotated[AsyncSession, Depends(platform_session)]


class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)
    auth_org_id: str = Field(min_length=1, max_length=255)
    branding: dict[str, Any] = Field(default_factory=dict)
    timezone: str = Field(default="UTC", max_length=100)
    domain: str = Field(default="generic", max_length=50)
    owner_email: str = Field(min_length=3, max_length=320)
    owner_display_name: str | None = Field(default=None, max_length=255)
    owner_password: SecretStr = Field(min_length=1, max_length=256)
    owner_password_confirm: SecretStr | None = Field(default=None, max_length=256)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        from app.core.timezones import normalize_timezone

        return normalize_timezone(value)

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        return normalize_domain(value)

    @field_validator("owner_email")
    @classmethod
    def normalize_owner_email(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if "@" not in cleaned:
            raise ValueError("A valid owner email is required")
        return cleaned

    @field_validator("owner_display_name")
    @classmethod
    def strip_owner_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def owner_passwords_match(self) -> "TenantCreate":
        confirm = (
            self.owner_password_confirm.get_secret_value()
            if self.owner_password_confirm is not None
            else None
        )
        validate_password(self.owner_password.get_secret_value(), confirm=confirm)
        return self


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None
    branding: dict[str, Any] | None = None
    timezone: str | None = Field(default=None, max_length=100)
    owner_email: str | None = Field(default=None, max_length=320)
    owner_display_name: str | None = Field(default=None, max_length=255)
    owner_password: SecretStr | None = Field(default=None, max_length=256)
    owner_password_confirm: SecretStr | None = Field(default=None, max_length=256)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from app.core.timezones import normalize_timezone

        return normalize_timezone(value)

    @field_validator("owner_email")
    @classmethod
    def normalize_owner_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lower()
        if not cleaned:
            return None
        if "@" not in cleaned:
            raise ValueError("A valid owner email is required")
        return cleaned

    @field_validator("owner_display_name")
    @classmethod
    def strip_owner_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def owner_passwords_match(self) -> "TenantUpdate":
        if self.owner_password is None:
            return self
        confirm = (
            self.owner_password_confirm.get_secret_value()
            if self.owner_password_confirm is not None
            else None
        )
        validate_password(self.owner_password.get_secret_value(), confirm=confirm)
        return self


class TenantOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    auth_org_id: str
    domain: str
    branding: dict[str, Any]
    timezone: str = "UTC"
    is_active: bool
    owner_email: str | None = None
    created_at: datetime
    updated_at: datetime


class PlatformAuditOut(BaseModel):
    id: uuid.UUID
    actor_id: str
    actor_email: str | None = None
    actor_name: str | None = None
    action: str
    tenant_id: uuid.UUID | None
    details: dict[str, Any]
    created_at: datetime


class TenantCatalogItemOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    kind: str
    status: str


class TenantImportIn(BaseModel):
    source_tenant_id: uuid.UUID
    destination_tenant_id: uuid.UUID
    team_ids: list[uuid.UUID] = Field(default_factory=list)
    workflow_ids: list[uuid.UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_selection(self) -> "TenantImportIn":
        if not self.team_ids and not self.workflow_ids:
            raise ValueError("Select at least one team or workflow to import")
        if self.source_tenant_id == self.destination_tenant_id:
            raise ValueError("Source and destination tenants must differ")
        return self


class TenantImportOut(BaseModel):
    agents: dict[str, str]
    teams: dict[str, str]
    workflows: dict[str, str]
    tools: dict[str, str]
    knowledge_bases: dict[str, str]
    warnings: list[str]
    counts: dict[str, int]


def tenant_out(tenant: Tenant, *, owner_email: str | None = None) -> TenantOut:
    payload = TenantOut.model_validate(tenant, from_attributes=True)
    return payload.model_copy(update={"owner_email": owner_email})


async def _find_owner_membership(
    session: AsyncSession, tenant_id: uuid.UUID
) -> Membership | None:
    return await session.scalar(
        select(Membership)
        .where(
            Membership.tenant_id == tenant_id,
            Membership.role == Role.tenant_admin,
        )
        .order_by(Membership.is_active.desc(), Membership.created_at.asc())
        .limit(1)
    )


async def _owner_email_for_tenant(
    session: AsyncSession, tenant_id: uuid.UUID
) -> str | None:
    await apply_tenant_guc(session, tenant_id)
    owner = await _find_owner_membership(session, tenant_id)
    if owner is None or not owner.email:
        return None
    return owner.email


async def serialize_tenant(session: AsyncSession, tenant: Tenant) -> TenantOut:
    return tenant_out(
        tenant, owner_email=await _owner_email_for_tenant(session, tenant.id)
    )


async def _provision_owner_identity(
    *,
    email: str,
    display_name: str,
    organization_id: str,
    password: str,
) -> tuple[str, IdentityAdminClient | None]:
    settings = get_settings()
    client = IdentityAdminClient(settings)
    if client.configured():
        try:
            identity = await client.provision_org_owner(
                email=email,
                display_name=display_name,
                organization_id=organization_id,
                password=password,
            )
        except IdentityProvisionError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        return identity.user_id, client
    if settings.auth_disabled:
        return f"local:{email}", None
    raise HTTPException(
        status_code=503,
        detail="Keycloak admin is not configured",
    )


async def _attach_owner(
    session: AsyncSession,
    actor: TenantContext,
    tenant: Tenant,
    *,
    email: str,
    display_name: str | None,
    password: str,
    timezone: str,
) -> str:
    from app.domains.access import assign_domain_default_teams

    owner_name = display_name or email.split("@", 1)[0]
    scoped = _scoped_context(actor, tenant)
    await apply_tenant_guc(session, tenant.id)
    memberships = MembershipRepository(session, scoped)
    if await email_taken_across_tenants(session, email):
        raise HTTPException(status_code=409, detail=EMAIL_ALREADY_IN_USE)

    owner_user_id, client = await _provision_owner_identity(
        email=email,
        display_name=owner_name,
        organization_id=tenant.auth_org_id,
        password=password,
    )
    try:
        await memberships.create(
            user_id=owner_user_id,
            display_name=owner_name,
            email=email,
            role=Role.tenant_admin,
            timezone=timezone,
        )
        await assign_domain_default_teams(session, scoped, owner_user_id)
    except HTTPException:
        if client is not None:
            await client.delete_user(owner_user_id)
        raise
    except ValueError as exc:
        if client is not None:
            await client.delete_user(owner_user_id)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        if client is not None:
            await client.delete_user(owner_user_id)
        raise
    return owner_user_id


async def _set_owner_password(
    *, user_id: str, email: str | None, password: str
) -> None:
    settings = get_settings()
    client = IdentityAdminClient(settings)
    if not client.configured():
        if settings.auth_disabled:
            return
        raise HTTPException(
            status_code=503,
            detail="Keycloak admin is not configured",
        )
    try:
        profile = await client.get_user(user_id)
        if profile is None and email:
            profile = await client.find_user_by_email(email)
        if profile is None:
            raise HTTPException(status_code=404, detail="Identity user not found")
        await client.set_password(str(profile.get("id") or user_id), password)
    except IdentityProvisionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _scoped_context(actor: TenantContext, tenant: Tenant) -> TenantContext:
    return TenantContext(
        tenant_id=tenant.id,
        user_id=actor.user_id,
        role=Role.platform_admin,
        auth_org_id=tenant.auth_org_id,
    )


async def _tenant_rls_session(tenant_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session, session.begin():
        if session.bind and session.bind.dialect.name == "postgresql":
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
        session.info["tenant_id"] = tenant_id
        yield session


async def audit(
    session: AsyncSession,
    context: TenantContext,
    action: str,
    tenant_id: uuid.UUID | None,
    details: dict[str, Any],
) -> None:
    session.add(
        PlatformAuditEvent(
            id=new_id(),
            actor_id=context.user_id,
            action=action,
            tenant_id=tenant_id,
            details=details,
        )
    )


@router.get("/tenants", response_model=list[TenantOut])
async def list_tenants(
    context: PlatformContext, session: PlatformSession
) -> list[TenantOut]:
    del context
    rows = (await session.scalars(select(Tenant).order_by(Tenant.name))).all()
    return [await serialize_tenant(session, row) for row in rows]


@router.post(
    "/tenants",
    response_model=TenantOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_tenant(
    payload: TenantCreate,
    context: PlatformContext,
    session: PlatformSession,
) -> TenantOut:
    slug = validate_slug(payload.slug)
    org_id = payload.auth_org_id.strip()
    owner_name = payload.owner_display_name or payload.owner_email.split("@", 1)[0]
    duplicate = await session.scalar(
        select(Tenant).where((Tenant.slug == slug) | (Tenant.auth_org_id == org_id))
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=409,
            detail="A tenant with this slug or organization already exists",
        )
    if await email_taken_across_tenants(session, payload.owner_email):
        raise HTTPException(status_code=409, detail=EMAIL_ALREADY_IN_USE)

    owner_user_id, identity_client = await _provision_owner_identity(
        email=payload.owner_email,
        display_name=owner_name,
        organization_id=org_id,
        password=payload.owner_password.get_secret_value(),
    )
    created_identity = identity_client is not None

    tenant = Tenant(
        id=new_id(),
        name=payload.name.strip(),
        slug=slug,
        auth_org_id=org_id,
        domain=payload.domain,
        branding={},
        timezone=payload.timezone,
        is_active=True,
    )
    session.add(tenant)
    try:
        await session.flush()
    except IntegrityError as exc:
        if created_identity and identity_client is not None:
            await identity_client.delete_user(owner_user_id)
        raise HTTPException(
            status_code=409,
            detail="A tenant with this slug or organization already exists",
        ) from exc

    try:
        provision = await apply_tenant_domain(
            session,
            tenant=tenant,
            actor_user_id=owner_user_id,
            domain=payload.domain,
            branding=payload.branding,
        )
        if session.bind and session.bind.dialect.name == "postgresql":
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant.id)},
            )
        session.info["tenant_id"] = tenant.id
        owner_context = TenantContext(
            tenant_id=tenant.id,
            user_id=owner_user_id,
            role=Role.tenant_admin,
            auth_org_id=tenant.auth_org_id,
        )
        memberships = MembershipRepository(session, owner_context)
        await memberships.create(
            user_id=owner_user_id,
            display_name=owner_name,
            email=payload.owner_email,
            role=Role.tenant_admin,
            timezone=payload.timezone,
        )
        billing = BillingService(session)
        await billing.provision_tenant_wallets(tenant.id)
    except Exception:
        if created_identity and identity_client is not None:
            await identity_client.delete_user(owner_user_id)
        raise

    await audit(
        session,
        context,
        "tenant.create",
        tenant.id,
        {
            "slug": tenant.slug,
            "auth_org_id": tenant.auth_org_id,
            "domain": payload.domain,
            "owner_email": payload.owner_email,
            "owner_user_id": owner_user_id,
            "provision": provision,
        },
    )
    await session.refresh(tenant)
    return tenant_out(tenant, owner_email=payload.owner_email)


@router.post("/tenants/import", response_model=TenantImportOut)
async def import_tenant_resources(
    payload: TenantImportIn,
    context: PlatformContext,
    session: PlatformSession,
) -> TenantImportOut:
    source = await session.get(Tenant, payload.source_tenant_id)
    destination = await session.get(Tenant, payload.destination_tenant_id)
    if source is None or not source.is_active:
        raise HTTPException(status_code=404, detail="Active source tenant not found")
    if destination is None or not destination.is_active:
        raise HTTPException(
            status_code=404, detail="Active destination tenant not found"
        )

    source_context = _scoped_context(context, source)
    dest_context = _scoped_context(context, destination)

    try:
        async for source_session in _tenant_rls_session(source.id):
            bundle = await collect_import_bundle(
                source_session,
                source_context,
                team_ids=payload.team_ids,
                workflow_ids=payload.workflow_ids,
            )
        async for dest_session in _tenant_rls_session(destination.id):
            result = await materialize_import_bundle(
                dest_session, dest_context, bundle
            )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    details = {
        "source_tenant_id": str(source.id),
        "destination_tenant_id": str(destination.id),
        "team_ids": [str(item) for item in payload.team_ids],
        "workflow_ids": [str(item) for item in payload.workflow_ids],
        "counts": result.as_dict()["counts"],
        "warnings": result.warnings,
    }
    await audit(session, context, "tenant.import", destination.id, details)
    return TenantImportOut.model_validate(result.as_dict())


@router.patch("/tenants/{tenant_id}", response_model=TenantOut)
async def update_tenant(
    tenant_id: uuid.UUID,
    payload: TenantUpdate,
    context: PlatformContext,
    session: PlatformSession,
) -> TenantOut:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if payload.is_active is False and tenant.id == context.tenant_id:
        raise HTTPException(
            status_code=409,
            detail="The platform admin's current organization cannot be suspended",
        )

    await apply_tenant_guc(session, tenant.id)
    owner = await _find_owner_membership(session, tenant.id)
    owner_email = payload.owner_email
    password = (
        payload.owner_password.get_secret_value()
        if payload.owner_password is not None
        else None
    )
    current_owner_email = (owner.email or "").strip().lower() if owner else ""
    creating_owner = False
    if owner is None:
        creating_owner = bool(owner_email or password)
    elif owner_email and owner_email != current_owner_email:
        creating_owner = True

    changes: dict[str, Any] = {}
    if creating_owner:
        if not owner_email:
            raise HTTPException(
                status_code=400,
                detail="Owner email is required when creating an owner",
            )
        if not password:
            raise HTTPException(
                status_code=400,
                detail="Owner password is required when creating an owner",
            )
        owner_user_id = await _attach_owner(
            session,
            context,
            tenant,
            email=owner_email,
            display_name=payload.owner_display_name,
            password=password,
            timezone=payload.timezone or tenant.timezone,
        )
        changes["owner_created"] = {
            "email": owner_email,
            "owner_user_id": owner_user_id,
        }
    elif owner is not None and password:
        await _set_owner_password(
            user_id=owner.user_id,
            email=owner.email,
            password=password,
        )
        changes["owner_password_set"] = True

    if payload.name is not None and payload.name.strip() != tenant.name:
        changes["name"] = {"from": tenant.name, "to": payload.name.strip()}
        tenant.name = payload.name.strip()
    if payload.is_active is not None and payload.is_active != tenant.is_active:
        changes["is_active"] = {"from": tenant.is_active, "to": payload.is_active}
        tenant.is_active = payload.is_active
    if payload.branding is not None and payload.branding != tenant.branding:
        changes["branding_updated"] = True
        tenant.branding = payload.branding
    if payload.timezone is not None and payload.timezone != tenant.timezone:
        changes["timezone"] = {"from": tenant.timezone, "to": payload.timezone}
        tenant.timezone = payload.timezone

    if changes:
        await session.flush()
        await audit(session, context, "tenant.update", tenant.id, changes)
        await session.refresh(tenant)
    return await serialize_tenant(session, tenant)


@router.post("/tenants/{tenant_id}/enter", response_model=TenantOut)
async def enter_tenant_workspace(
    tenant_id: uuid.UUID,
    context: PlatformContext,
    session: PlatformSession,
) -> TenantOut:
    tenant = await session.scalar(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.is_active.is_(True))
    )
    if tenant is None:
        raise HTTPException(status_code=404, detail="Active tenant not found")
    await audit(
        session,
        context,
        "tenant.workspace.enter",
        tenant.id,
        {"slug": tenant.slug},
    )
    return await serialize_tenant(session, tenant)


@router.get(
    "/tenants/{tenant_id}/catalog",
    response_model=list[TenantCatalogItemOut],
)
async def get_tenant_catalog(
    tenant_id: uuid.UUID,
    context: PlatformContext,
    session: PlatformSession,
) -> list[TenantCatalogItemOut]:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None or not tenant.is_active:
        raise HTTPException(status_code=404, detail="Active tenant not found")
    scoped = _scoped_context(context, tenant)
    async for tenant_session in _tenant_rls_session(tenant.id):
        items = await list_tenant_catalog(tenant_session, scoped)
        return [
            TenantCatalogItemOut(
                id=item.id,
                name=item.name,
                slug=item.slug,
                kind=item.kind,
                status=item.status,
            )
            for item in items
        ]
    return []


@router.get("/audit", response_model=list[PlatformAuditOut])
async def list_platform_audit(
    context: PlatformContext,
    session: PlatformSession,
    limit: int = 50,
) -> list[PlatformAuditOut]:
    del context
    safe_limit = min(max(limit, 1), 200)
    rows = (
        await session.scalars(
            select(PlatformAuditEvent)
            .order_by(PlatformAuditEvent.created_at.desc())
            .limit(safe_limit)
        )
    ).all()
    profiles = await resolve_membership_profiles(
        session, [row.actor_id for row in rows]
    )
    events: list[PlatformAuditOut] = []
    for row in rows:
        profile = profiles.get(row.actor_id)
        events.append(
            PlatformAuditOut(
                id=row.id,
                actor_id=row.actor_id,
                actor_email=profile.email if profile else None,
                actor_name=profile.name if profile else None,
                action=row.action,
                tenant_id=row.tenant_id,
                details=row.details or {},
                created_at=row.created_at,
            )
        )
    return events


@router.get("/sandbox-packages", response_model=list[PlatformPythonPackageOut])
async def list_sandbox_packages(
    context: PlatformContext,
    session: PlatformSession,
    active_only: bool = False,
) -> list[PlatformPythonPackageOut]:
    del context
    rows = await PlatformPythonPackageRepository(session).list(active_only=active_only)
    return [
        PlatformPythonPackageOut.model_validate(row, from_attributes=True) for row in rows
    ]


@router.post(
    "/sandbox-packages",
    response_model=PlatformPythonPackageOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_sandbox_package(
    payload: PlatformPythonPackageIn,
    context: PlatformContext,
    session: PlatformSession,
) -> PlatformPythonPackageOut:
    repo = PlatformPythonPackageRepository(session)
    try:
        row = await repo.create(payload.model_dump())
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="Package name/version already exists on the allowlist",
        ) from exc
    await audit(
        session,
        context,
        "sandbox_package.create",
        None,
        {"name": row.name, "version": row.version, "sha256": row.sha256},
    )
    await session.refresh(row)
    return PlatformPythonPackageOut.model_validate(row, from_attributes=True)


@router.patch("/sandbox-packages/{package_id}", response_model=PlatformPythonPackageOut)
async def update_sandbox_package(
    package_id: uuid.UUID,
    payload: PlatformPythonPackageUpdateIn,
    context: PlatformContext,
    session: PlatformSession,
) -> PlatformPythonPackageOut:
    changes = payload.model_dump(exclude_unset=True)
    row = await PlatformPythonPackageRepository(session).update(package_id, changes)
    if row is None:
        raise HTTPException(status_code=404, detail="Package not found")
    await audit(
        session,
        context,
        "sandbox_package.update",
        None,
        {"id": str(row.id), "fields": sorted(changes), "active": row.active},
    )
    return PlatformPythonPackageOut.model_validate(row, from_attributes=True)


@router.get("/sandbox-packages/catalog", response_model=list[PlatformPythonPackageOut])
async def catalog_active_sandbox_packages(
    context: PlatformContext,
    session: PlatformSession,
) -> list[PlatformPythonPackageOut]:
    """Active packages for editors (platform admin). Tenant admins use the tools route."""
    del context
    rows = await PlatformPythonPackageRepository(session).list(active_only=True)
    return [
        PlatformPythonPackageOut.model_validate(row, from_attributes=True) for row in rows
    ]


class PlatformPlanUpsertIn(BaseModel):
    slug: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    monthly_price_cents: int = Field(ge=0, default=0)
    included_credits_monthly: int = Field(ge=0, default=0)
    credits_per_1k_input_tokens: int = Field(ge=1, default=10)
    credits_per_1k_output_tokens: int = Field(ge=1, default=30)
    credit_pack_credits: int = Field(ge=1, default=1000)
    credit_pack_price_cents: int = Field(ge=0, default=1000)
    is_active: bool = True


class PlatformWalletOut(BaseModel):
    tenant_id: uuid.UUID
    balance_credits: int
    allowance_remaining: int
    available_credits: int
    subscription_status: str
    plan_id: uuid.UUID | None


class PlatformGrantIn(BaseModel):
    credits: int = Field(ge=1, le=10_000_000)
    description: str = Field(default="Platform credit grant", max_length=500)


@router.get("/billing/plans")
async def list_platform_billing_plans(
    context: PlatformContext,
    session: PlatformSession,
) -> list[dict[str, Any]]:
    del context
    from app.api.billing import _plan_out
    from app.billing.service import BillingService

    billing = BillingService(session)
    await billing.ensure_default_platform_plan()
    plans = await billing.list_plans(scope="platform")
    return [_plan_out(plan).model_dump() for plan in plans]


@router.put("/billing/plans/{slug}")
async def upsert_platform_billing_plan(
    slug: str,
    payload: PlatformPlanUpsertIn,
    context: PlatformContext,
    session: PlatformSession,
) -> dict[str, Any]:
    from app.api.billing import _plan_out
    from app.billing.service import BillingService

    billing = BillingService(session)
    plan = await billing.upsert_plan(
        {
            "scope": "platform",
            "tenant_id": None,
            "slug": slug,
            **payload.model_dump(),
        }
    )
    await audit(
        session,
        context,
        "billing.plan.upsert",
        None,
        {"slug": slug, "name": plan.name},
    )
    return _plan_out(plan).model_dump()


@router.get("/billing/tenants/{tenant_id}/wallet", response_model=PlatformWalletOut)
async def get_tenant_platform_wallet(
    tenant_id: uuid.UUID,
    context: PlatformContext,
    session: PlatformSession,
) -> PlatformWalletOut:
    del context, session
    from app.billing.service import BillingService, wallet_available

    async for tenant_session in _tenant_rls_session(tenant_id):
        billing = BillingService(tenant_session)
        wallet = await billing.provision_tenant_wallets(tenant_id)
        return PlatformWalletOut(
            tenant_id=tenant_id,
            balance_credits=wallet.balance_credits,
            allowance_remaining=wallet.allowance_remaining,
            available_credits=wallet_available(wallet),
            subscription_status=wallet.subscription_status,
            plan_id=wallet.plan_id,
        )
    raise HTTPException(status_code=404, detail="Tenant not found")


@router.post("/billing/tenants/{tenant_id}/grant", response_model=PlatformWalletOut)
async def grant_tenant_platform_credits(
    tenant_id: uuid.UUID,
    payload: PlatformGrantIn,
    context: PlatformContext,
    session: PlatformSession,
) -> PlatformWalletOut:
    from app.billing.service import BillingService, wallet_available

    async for tenant_session in _tenant_rls_session(tenant_id):
        billing = BillingService(tenant_session)
        wallet = await billing.grant_credits(
            tenant_id=tenant_id,
            owner_type="tenant",
            owner_id=str(tenant_id),
            credits=payload.credits,
            created_by=context.user_id,
            description=payload.description,
        )
        await audit(
            session,
            context,
            "billing.tenant.grant",
            tenant_id,
            {"credits": payload.credits, "description": payload.description},
        )
        return PlatformWalletOut(
            tenant_id=tenant_id,
            balance_credits=wallet.balance_credits,
            allowance_remaining=wallet.allowance_remaining,
            available_credits=wallet_available(wallet),
            subscription_status=wallet.subscription_status,
            plan_id=wallet.plan_id,
        )
    raise HTTPException(status_code=404, detail="Tenant not found")
