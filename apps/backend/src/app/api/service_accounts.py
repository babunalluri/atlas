import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    ServiceAccountCreatedOut,
    ServiceAccountCreateIn,
    ServiceAccountOut,
)
from app.auth.dependencies import require_tenant
from app.auth.service_accounts import mint_service_account_token
from app.db.repositories import ServiceAccountRepository
from app.db.session import tenant_session
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/service-accounts", tags=["service-accounts"])
TenantSession = Annotated[AsyncSession, Depends(tenant_session)]
Context = Annotated[TenantContext, Depends(require_tenant)]


def _authorize(context: TenantContext, scope: str) -> None:
    if context.principal_type == "service_account":
        allowed = context.has_scope(scope)
    else:
        allowed = context.can_administer()
    if not allowed:
        raise HTTPException(status_code=403, detail="Insufficient service account permission")


def _serialize(account: object) -> ServiceAccountOut:
    return ServiceAccountOut.model_validate(account, from_attributes=True)


@router.get("", response_model=list[ServiceAccountOut])
async def list_service_accounts(
    context: Context, session: TenantSession
) -> list[ServiceAccountOut]:
    _authorize(context, "service_accounts:read")
    rows = await ServiceAccountRepository(session, context).list_accounts()
    return [_serialize(row) for row in rows]


@router.post("", response_model=ServiceAccountCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_service_account(
    payload: ServiceAccountCreateIn,
    context: Context,
    session: TenantSession,
) -> ServiceAccountCreatedOut:
    _authorize(context, "service_accounts:write")
    minted = mint_service_account_token(context.tenant_id)
    account = await ServiceAccountRepository(session, context).create(
        name=payload.name,
        token_prefix=minted.token_prefix,
        token_hash=minted.token_hash,
        scopes=payload.scopes,
        expires_at=payload.expires_at,
    )
    data = _serialize(account).model_dump()
    return ServiceAccountCreatedOut(**data, token=minted.token)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_service_account(
    account_id: uuid.UUID,
    context: Context,
    session: TenantSession,
) -> None:
    _authorize(context, "service_accounts:delete")
    account = await ServiceAccountRepository(session, context).revoke(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Active service account not found")
