"""Self-serve workspace provisioning for unprovisioned auth organizations."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.dependencies import AuthClaims, resolve_auth_identity
from app.db.models import PlatformAuditEvent, Tenant
from app.db.session import SessionFactory
from app.domains.setup import apply_tenant_domain
from app.domains.types import WORKSPACE_DOMAINS, normalize_domain
from app.tenancy.ids import new_id, validate_slug

router = APIRouter(prefix="/admin/onboarding", tags=["onboarding"])


class WorkspaceCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)
    domain: Literal["generic", "stock_broker", "dental_clinic"] = "generic"

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        return normalize_domain(value)


class OnboardingStatusOut(BaseModel):
    provisioned: bool
    can_create: bool
    org_id: str | None = None
    org_role: str | None = None
    tenant_id: UUID | None = None
    tenant_slug: str | None = None
    tenant_name: str | None = None
    tenant_domain: str | None = None


class WorkspaceOut(BaseModel):
    id: UUID
    name: str
    slug: str
    auth_org_id: str
    domain: str
    branding: dict[str, Any]
    is_active: bool


def _can_self_serve(claims: AuthClaims) -> bool:
    if claims.org_role in {"org:admin", "admin", "org:owner"}:
        return True
    platform_flag = claims.platform_admin
    if platform_flag is None:
        platform_flag = claims.metadata.get("platform_admin")
    return platform_flag is True or str(platform_flag).lower() in {"true", "1", "yes"}


@router.get("/status", response_model=OnboardingStatusOut)
async def onboarding_status(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> OnboardingStatusOut:
    claims = await resolve_auth_identity(request, authorization=authorization)
    async with SessionFactory() as session:
        tenant = await session.scalar(
            select(Tenant).where(
                Tenant.auth_org_id == claims.org_id,
                Tenant.is_active.is_(True),
            )
        )
    return OnboardingStatusOut(
        provisioned=tenant is not None,
        can_create=_can_self_serve(claims),
        org_id=claims.org_id,
        org_role=claims.org_role,
        tenant_id=tenant.id if tenant else None,
        tenant_slug=tenant.slug if tenant else None,
        tenant_name=tenant.name if tenant else None,
        tenant_domain=getattr(tenant, "domain", None) if tenant else None,
    )


@router.get("/domains")
async def list_onboarding_domains() -> list[dict[str, str]]:
    from app.domains.types import DOMAIN_LABELS

    return [{"id": domain, "label": DOMAIN_LABELS[domain]} for domain in WORKSPACE_DOMAINS]


@router.post(
    "/workspace",
    response_model=WorkspaceOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace(
    payload: WorkspaceCreateIn,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> WorkspaceOut:
    """Create a tenant for the caller's auth org when none exists yet.

    auth_org_id is taken from the verified JWT — never from the request body —
    so a user cannot attach another organization's id.
    """
    claims = await resolve_auth_identity(request, authorization=authorization)
    if not _can_self_serve(claims):
        raise HTTPException(
            status_code=403,
            detail="Only an organization admin can create a workspace",
        )

    try:
        slug = validate_slug(payload.slug)
        domain = normalize_domain(payload.domain)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async with SessionFactory() as session:
        existing = await session.scalar(
            select(Tenant).where(Tenant.auth_org_id == claims.org_id)
        )
        if existing is not None:
            if not existing.is_active:
                raise HTTPException(
                    status_code=409,
                    detail="This organization already has a suspended workspace",
                )
            raise HTTPException(
                status_code=409,
                detail="This organization already has a workspace",
            )

        tenant = Tenant(
            id=new_id(),
            name=payload.name.strip(),
            slug=slug,
            auth_org_id=claims.org_id,
            domain=domain,
            branding={},
            is_active=True,
        )
        session.add(tenant)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=409,
                detail="A workspace with this slug or organization already exists",
            ) from exc

        provision = await apply_tenant_domain(
            session,
            tenant=tenant,
            actor_user_id=claims.sub,
            domain=domain,
        )

        session.add(
            PlatformAuditEvent(
                id=new_id(),
                actor_id=claims.sub,
                action="tenant.self_serve.create",
                tenant_id=tenant.id,
                details={
                    "slug": tenant.slug,
                    "auth_org_id": tenant.auth_org_id,
                    "domain": domain,
                    "provision": provision,
                },
            )
        )
        await session.commit()
        await session.refresh(tenant)
        return WorkspaceOut(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            auth_org_id=tenant.auth_org_id,
            domain=tenant.domain,
            branding=tenant.branding or {},
            is_active=tenant.is_active,
        )
