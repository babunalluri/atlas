"""Public end-user identity (OTP) for any tenant's customers."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.public_chat import _guest_user_id, _public_run_context
from app.core.rate_limit import limiter
from app.core.settings import get_settings
from app.identity.service import IdentityService

router = APIRouter(prefix="/public", tags=["public-identity"])


async def _rate_limit_identity(*, tenant_id: UUID, guest_user_id: str, host: str) -> None:
    settings = get_settings()
    per_guest = max(1, min(20, settings.public_chat_rate_limit_per_minute))
    await limiter.async_hit(f"public-identity:guest:{tenant_id}:{guest_user_id}", limit=per_guest)
    await limiter.async_hit(f"public-identity:ip:{host}", limit=per_guest * 3)


class ChallengeIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    session_id: str = Field(min_length=8, max_length=255)


class ChallengeOut(BaseModel):
    email: str
    expires_at: datetime
    debug_code: str | None = None


class VerifyIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    code: str = Field(min_length=4, max_length=12)
    session_id: str = Field(min_length=8, max_length=255)


class VerifyOut(BaseModel):
    verified: bool
    end_user_id: str
    email: str
    display_name: str
    metadata: dict


class IdentityStatusOut(BaseModel):
    verified: bool
    end_user_id: str | None = None
    email: str | None = None
    display_name: str | None = None
    metadata: dict | None = None


@router.post(
    "/t/{tenant_slug}/identity/challenge",
    response_model=ChallengeOut,
)
async def request_identity_challenge(
    tenant_slug: str,
    payload: ChallengeIn,
    request: Request,
    x_guest_id: Annotated[str | None, Header()] = None,
) -> ChallengeOut:
    host = request.client.host if request.client else "anon"
    guest_user_id = _guest_user_id(x_guest_id, fallback_host=host)
    async with _public_run_context(tenant_slug, guest_user_id=guest_user_id) as (
        session,
        tenant,
        context,
    ):
        await _rate_limit_identity(
            tenant_id=tenant.id, guest_user_id=guest_user_id, host=host
        )
        try:
            result = await IdentityService(session, context).request_challenge(
                email=str(payload.email),
                external_session_id=payload.session_id,
                guest_user_id=guest_user_id,
            )
            await session.commit()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return ChallengeOut(
            email=result.email,
            expires_at=result.expires_at,
            debug_code=result.debug_code,
        )


@router.post(
    "/t/{tenant_slug}/identity/verify",
    response_model=VerifyOut,
)
async def verify_identity(
    tenant_slug: str,
    payload: VerifyIn,
    request: Request,
    x_guest_id: Annotated[str | None, Header()] = None,
) -> VerifyOut:
    host = request.client.host if request.client else "anon"
    guest_user_id = _guest_user_id(x_guest_id, fallback_host=host)
    async with _public_run_context(tenant_slug, guest_user_id=guest_user_id) as (
        session,
        tenant,
        context,
    ):
        await _rate_limit_identity(
            tenant_id=tenant.id, guest_user_id=guest_user_id, host=host
        )
        try:
            result = await IdentityService(session, context).verify_challenge(
                email=str(payload.email),
                code=payload.code,
                external_session_id=payload.session_id,
                guest_user_id=guest_user_id,
            )
            await session.commit()
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return VerifyOut(
            verified=True,
            end_user_id=result.end_user_id,
            email=result.email,
            display_name=result.display_name,
            metadata=result.metadata,
        )


@router.get(
    "/t/{tenant_slug}/identity/status",
    response_model=IdentityStatusOut,
)
async def identity_status(
    tenant_slug: str,
    session_id: str,
    request: Request,
    x_guest_id: Annotated[str | None, Header()] = None,
) -> IdentityStatusOut:
    host = request.client.host if request.client else "anon"
    guest_user_id = _guest_user_id(x_guest_id, fallback_host=host)
    async with _public_run_context(tenant_slug, guest_user_id=guest_user_id) as (
        session,
        _tenant,
        context,
    ):
        user = await IdentityService(session, context).resolve_for_session(
            external_session_id=session_id,
            guest_user_id=guest_user_id,
        )
        await session.commit()
        if user is None:
            return IdentityStatusOut(verified=False)
        return IdentityStatusOut(
            verified=True,
            end_user_id=str(user.id),
            email=user.email,
            display_name=user.display_name,
            metadata=dict(user.user_metadata or {}),
        )
