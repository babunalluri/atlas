import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.db.models import PlatformAuditEvent, Role, Tenant
from app.db.repositories import PlatformPythonPackageRepository
from app.db.session import SessionFactory
from app.platform.tenant_import import (
    collect_import_bundle,
    list_tenant_catalog,
    materialize_import_bundle,
)
from app.tenancy.context import TenantContext
from app.tenancy.ids import new_id, validate_slug
from app.api.schemas import (
    PlatformPythonPackageIn,
    PlatformPythonPackageOut,
    PlatformPythonPackageUpdateIn,
)

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

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        from app.core.timezones import normalize_timezone

        return normalize_timezone(value)


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None
    branding: dict[str, Any] | None = None
    timezone: str | None = Field(default=None, max_length=100)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from app.core.timezones import normalize_timezone

        return normalize_timezone(value)


class TenantOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    auth_org_id: str
    branding: dict[str, Any]
    timezone: str = "UTC"
    is_active: bool
    created_at: datetime
    updated_at: datetime


class PlatformAuditOut(BaseModel):
    id: uuid.UUID
    actor_id: str
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


def tenant_out(tenant: Tenant) -> TenantOut:
    return TenantOut.model_validate(tenant, from_attributes=True)


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
    rows = await session.scalars(select(Tenant).order_by(Tenant.name))
    return [tenant_out(row) for row in rows.all()]


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
    tenant = Tenant(
        id=new_id(),
        name=payload.name.strip(),
        slug=validate_slug(payload.slug),
        auth_org_id=payload.auth_org_id.strip(),
        branding=payload.branding,
        timezone=payload.timezone,
        is_active=True,
    )
    session.add(tenant)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="A tenant with this slug or organization already exists",
        ) from exc
    await audit(
        session,
        context,
        "tenant.create",
        tenant.id,
        {"slug": tenant.slug, "auth_org_id": tenant.auth_org_id},
    )
    from sqlalchemy import text

    from app.billing.service import BillingService

    if session.bind and session.bind.dialect.name == "postgresql":
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant.id)},
        )
    session.info["tenant_id"] = tenant.id
    billing = BillingService(session)
    await billing.provision_tenant_wallets(tenant.id)
    await session.refresh(tenant)
    return tenant_out(tenant)


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

    changes: dict[str, Any] = {}
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
    return tenant_out(tenant)


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
    return tenant_out(tenant)


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
    rows = await session.scalars(
        select(PlatformAuditEvent)
        .order_by(PlatformAuditEvent.created_at.desc())
        .limit(safe_limit)
    )
    return [
        PlatformAuditOut.model_validate(row, from_attributes=True)
        for row in rows.all()
    ]


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
