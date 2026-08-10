"""Admin API for verified end customers (public chat/email identity)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import EndCustomerOut, EndCustomerUpdateIn
from app.auth.dependencies import require_roles
from app.db.models import EndUser, Role
from app.db.repositories import EndUserRepository
from app.db.session import tenant_session
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/customers", tags=["admin-customers"])
AdminContext = Annotated[
    TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
]
TenantSession = Annotated[AsyncSession, Depends(tenant_session)]


def _out(row: EndUser) -> EndCustomerOut:
    return EndCustomerOut(
        id=row.id,
        email=row.email,
        display_name=row.display_name,
        email_verified_at=row.email_verified_at,
        is_active=row.is_active,
        metadata=dict(row.user_metadata or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("", response_model=list[EndCustomerOut])
async def list_customers(
    context: AdminContext, session: TenantSession
) -> list[EndCustomerOut]:
    rows = await EndUserRepository(session, context).list(limit=500)
    return [_out(row) for row in rows]


@router.get("/{customer_id}", response_model=EndCustomerOut)
async def get_customer(
    customer_id: uuid.UUID,
    context: AdminContext,
    session: TenantSession,
) -> EndCustomerOut:
    row = await EndUserRepository(session, context).get(customer_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return _out(row)


@router.patch("/{customer_id}", response_model=EndCustomerOut)
async def update_customer(
    customer_id: uuid.UUID,
    body: EndCustomerUpdateIn,
    context: AdminContext,
    session: TenantSession,
) -> EndCustomerOut:
    row = await EndUserRepository(session, context).update_profile(
        customer_id,
        display_name=body.display_name,
        metadata_patch=body.metadata,
        is_active=body.is_active,
        allow_inactive=True,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return _out(row)
