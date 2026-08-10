import asyncio
import uuid
from collections.abc import Callable
from typing import Annotated, Any

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from jwt import PyJWKClient
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from app.auth.service_accounts import PAT_PREFIX, authenticate_service_account
from app.core.settings import Settings, get_settings
from app.db.models import Role, Tenant
from app.db.session import SessionFactory
from app.tenancy.context import TenantContext, reset_tenant_context, set_tenant_context


class ClerkClaims(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sub: str
    org_id: str
    org_role: str = "org:member"
    scopes: list[str] = Field(default_factory=list)
    platform_admin: bool | str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


_jwks_clients: dict[str, PyJWKClient] = {}


def _jwks_client(jwks_url: str) -> PyJWKClient:
    client = _jwks_clients.get(jwks_url)
    if client is None:
        # Reuse across requests — constructing a new client per decode re-fetches JWKS.
        client = PyJWKClient(jwks_url, cache_keys=True, lifespan=60 * 60)
        _jwks_clients[jwks_url] = client
    return client


def _decode(token: str, settings: Settings) -> dict[str, Any]:
    if not settings.effective_jwks_url:
        raise jwt.InvalidTokenError("JWKS URL is not configured")
    key = _jwks_client(settings.effective_jwks_url).get_signing_key_from_jwt(token).key
    options = {"require": ["exp", "iat", "sub"]}
    kwargs: dict[str, Any] = {
        "algorithms": ["RS256"],
        "issuer": settings.clerk_issuer or None,
        "options": options,
    }
    if settings.clerk_audience:
        kwargs["audience"] = settings.clerk_audience
    return jwt.decode(token, key, **kwargs)


def _email_from_payload(payload: dict[str, Any]) -> str | None:
    for key in ("email", "email_address", "primary_email_address"):
        value = payload.get(key)
        if isinstance(value, str) and "@" in value:
            return value.strip().lower()
    return None


def _role(claims: ClerkClaims) -> Role:
    # Platform elevation must only be emitted by the server-controlled JWT template.
    platform_flag = claims.platform_admin
    if platform_flag is None:
        platform_flag = claims.metadata.get("platform_admin")
    if platform_flag is True or str(platform_flag).lower() in {"true", "1", "yes"}:
        return Role.platform_admin
    if claims.org_role in {"org:admin", "admin", "org:owner"}:
        return Role.tenant_admin
    return Role.end_user


def _flatten_clerk_payload(payload: dict[str, Any]) -> dict[str, Any]:
    org_from_o: str | None = None
    org_role_from_o: str | None = None
    if "o" in payload and isinstance(payload["o"], dict):
        org_from_o = payload["o"].get("id") or payload["o"].get("org_id")
        org_role_from_o = payload["o"].get("rol") or payload["o"].get("role")
    next_payload = payload
    if not payload.get("org_id") and org_from_o:
        next_payload = {**next_payload, "org_id": org_from_o}
    if not payload.get("org_role") and org_role_from_o:
        next_payload = {**next_payload, "org_role": org_role_from_o}
    return next_payload


async def resolve_clerk_identity(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    settings: Settings | None = None,
) -> ClerkClaims:
    """Validate Clerk (or dev) identity without requiring a provisioned tenant.

    Used for self-serve onboarding. clerk_org_id always comes from verified
    claims — never from the request body.
    """
    if settings is None:
        settings = get_settings()
    bearer_token = (
        authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    )
    if settings.auth_disabled:
        user_id = request.headers.get("x-dev-user-id", "dev-user")
        org_role = request.headers.get("x-dev-org-role", "org:admin")
        # Prefer the active dev tenant's org so provisioned workspaces do not
        # look unprovisioned when NEXT_PUBLIC_DEV_AUTH is enabled.
        tenant_value = request.headers.get("x-dev-tenant-id")
        if tenant_value:
            try:
                tenant_id = uuid.UUID(tenant_value)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid tenant ID") from exc
            async with SessionFactory() as session:
                tenant = await session.get(Tenant, tenant_id)
            if tenant is not None and tenant.is_active:
                return ClerkClaims(
                    sub=user_id,
                    org_id=tenant.clerk_org_id,
                    org_role=org_role,
                )
        org_id = request.headers.get("x-dev-org-id", "org_unprovisioned_dev")
        return ClerkClaims(sub=user_id, org_id=org_id, org_role=org_role)
    if not bearer_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token"
        )
    if bearer_token.startswith(PAT_PREFIX):
        raise HTTPException(
            status_code=403,
            detail="Service accounts cannot create workspaces",
        )
    try:
        payload = await asyncio.to_thread(_decode, bearer_token, settings)
        claims = ClerkClaims.model_validate(_flatten_clerk_payload(payload))
    except (jwt.PyJWTError, ValidationError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    if not claims.org_id:
        raise HTTPException(status_code=403, detail="Organization claim is required")
    return claims


async def require_tenant(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_platform_tenant_id: Annotated[str | None, Header()] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
) -> TenantContext:
    if settings is None:
        settings = get_settings()
    existing = getattr(request.state, "tenant", None)
    if isinstance(existing, TenantContext):
        return existing

    bearer_token = (
        authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    )
    payload: dict[str, Any]
    if bearer_token and bearer_token.startswith(PAT_PREFIX):
        context = await authenticate_service_account(bearer_token)
        if context is None:
            raise HTTPException(status_code=401, detail="Invalid service account token")
        payload = {
            "sub": context.user_id,
            "org_id": context.clerk_org_id,
            "scopes": list(context.scopes),
            "principal_type": context.principal_type,
        }
    elif settings.auth_disabled:
        user_id = request.headers.get("x-dev-user-id", "dev-user")
        role_header = request.headers.get("x-dev-role", Role.platform_admin.value)
        tenant_value = request.headers.get("x-dev-tenant-id")
        if not tenant_value:
            raise HTTPException(status_code=401, detail="x-dev-tenant-id is required")
        try:
            role = Role(role_header)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid x-dev-role") from exc
        if x_platform_tenant_id:
            if role != Role.platform_admin:
                raise HTTPException(
                    status_code=403,
                    detail="Only platform admins may select another tenant",
                )
            tenant_value = x_platform_tenant_id
        try:
            tenant_id = uuid.UUID(tenant_value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid tenant ID") from exc
        context = TenantContext(tenant_id, user_id, role, "dev")
        payload = {
            "sub": user_id,
            "org_id": "dev",
            "org_role": role.value,
            "scopes": list(context.scopes),
        }
    else:
        if not bearer_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token"
            )
        try:
            payload = await asyncio.to_thread(_decode, bearer_token, settings)
            claims = ClerkClaims.model_validate(_flatten_clerk_payload(payload))
        except (jwt.PyJWTError, ValidationError, ValueError) as exc:
            raise HTTPException(status_code=401, detail="Invalid token") from exc
        if not claims.org_id:
            raise HTTPException(status_code=403, detail="Organization claim is required")

        role = _role(claims)
        membership_role: Role | None = None
        # Snapshot scalars before the session closes — commit/rollback expires
        # ORM attributes and accessing them after raises DetachedInstanceError
        # (browser then often surfaces that 500 as a vague "Failed to fetch").
        resolved_tenant_id: uuid.UUID | None = None
        resolved_clerk_org_id: str | None = None
        home_org_provisioned = False
        async with SessionFactory() as session:
            home_tenant = await session.scalar(
                select(Tenant).where(
                    Tenant.clerk_org_id == claims.org_id,
                    Tenant.is_active.is_(True),
                )
            )
            home_org_provisioned = home_tenant is not None
            tenant = home_tenant
            if x_platform_tenant_id:
                if role != Role.platform_admin:
                    raise HTTPException(
                        status_code=403,
                        detail="Only platform admins may select another tenant",
                    )
                try:
                    selected_tenant_id = uuid.UUID(x_platform_tenant_id)
                except ValueError as exc:
                    raise HTTPException(
                        status_code=400, detail="Invalid platform tenant ID"
                    ) from exc
                tenant = await session.scalar(
                    select(Tenant).where(
                        Tenant.id == selected_tenant_id,
                        Tenant.is_active.is_(True),
                    )
                )
                if tenant is None:
                    raise HTTPException(
                        status_code=404, detail="Selected tenant is inactive or missing"
                    )
            if tenant is not None:
                resolved_tenant_id = tenant.id
                resolved_clerk_org_id = tenant.clerk_org_id
                email = _email_from_payload(payload)
                try:
                    from app.auth.identity_admin import IdentityAdminClient
                    from app.db.repositories import MembershipRepository
                    from app.db.session import apply_tenant_guc

                    await apply_tenant_guc(session, resolved_tenant_id)
                    claim_context = TenantContext(
                        resolved_tenant_id,
                        claims.sub,
                        role,
                        resolved_clerk_org_id,
                        scopes=tuple(claims.scopes),
                    )
                    membership_repo = MembershipRepository(session, claim_context)
                    # Fast path: already-linked members skip Clerk profile fetches.
                    membership = await membership_repo.get_by_user_id(claims.sub)
                    if membership is not None:
                        membership_role = (
                            membership.role if membership.is_active else Role.end_user
                        )
                    else:
                        client = IdentityAdminClient(settings)
                        if not email and client.configured():
                            profile = await client.get_user(claims.sub)
                            if profile is not None:
                                email = IdentityAdminClient.primary_email(profile)
                        if email:
                            claimed = await membership_repo.claim_pending_by_email(
                                email=email, user_id=claims.sub
                            )
                            if claimed is not None:
                                await session.commit()
                                membership_role = (
                                    claimed.role if claimed.is_active else Role.end_user
                                )
                except ValueError:
                    await session.rollback()
                except Exception:
                    # Claim is best-effort; never block authenticated requests.
                    await session.rollback()
        if not home_org_provisioned:
            raise HTTPException(
                status_code=403,
                detail=f"Organization is not provisioned ({claims.org_id})",
            )
        if resolved_tenant_id is None or resolved_clerk_org_id is None:
            raise HTTPException(
                status_code=403,
                detail=f"Organization is not provisioned ({claims.org_id})",
            )
        if role != Role.platform_admin and membership_role is not None:
            role = membership_role
        context = TenantContext(
            resolved_tenant_id,
            claims.sub,
            role,
            resolved_clerk_org_id,
            scopes=tuple(claims.scopes),
        )

    token = set_tenant_context(context)
    request.state.tenant = context
    # AgentOS treats request.state.user_id as authoritative over form input.
    # Namespace it so native session/memory/trace rows cannot collide across
    # organizations even when Clerk user IDs or client session IDs repeat.
    request.state.user_id = f"{context.tenant_id}:{context.user_id}"
    request.state.claims = payload
    request.state.scopes = list(context.scopes)
    request.state.tenant_context_token = token
    return context


def require_roles(*roles: Role) -> Callable[..., Any]:
    async def dependency(
        context: Annotated[TenantContext, Depends(require_tenant)],
    ) -> TenantContext:
        service_admin = (
            context.principal_type == "service_account"
            and bool({Role.platform_admin, Role.tenant_admin}.intersection(roles))
            and context.can_administer()
        )
        if context.role not in roles and not service_admin:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return context

    return dependency


def clear_tenant_context(request: Request) -> None:
    token = getattr(request.state, "tenant_context_token", None)
    if token is not None:
        reset_tenant_context(token)
