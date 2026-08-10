"""Org-admin vault management for another user's secrets/variables.

Values remain write-only: admins can set/delete keys but never read plaintext.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.user_vault import (
    VaultEntryOut,
    VaultUpsertIn,
    _cipher,
    _validate_name,
)
from app.auth.dependencies import require_roles
from app.core.settings import Settings, get_settings
from app.credentials.provider import AwsKmsCipher
from app.db.models import Role
from app.db.repositories import MembershipRepository, UserVaultRepository
from app.db.session import tenant_session
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/vault", tags=["admin-vault"])
AdminContext = Annotated[
    TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
]


class AdminVaultTargetOut(BaseModel):
    user_id: str
    display_name: str = ""
    email: str = ""


async def _require_membership_user(
    session: AsyncSession,
    context: TenantContext,
    user_id: str,
) -> None:
    users = await MembershipRepository(session, context).list_users()
    if not any(row.user_id == user_id for row in users):
        raise HTTPException(
            status_code=404,
            detail="User is not a member of this organization",
        )


@router.get("/users", response_model=list[AdminVaultTargetOut])
async def list_vault_targets(
    context: AdminContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> list[AdminVaultTargetOut]:
    rows = await MembershipRepository(session, context).list_users()
    return [
        AdminVaultTargetOut(
            user_id=row.user_id,
            display_name=row.display_name or "",
            email=row.email or "",
        )
        for row in rows
        if row.user_id and not str(row.user_id).startswith("invite:")
    ]


@router.get("/users/{user_id}", response_model=list[VaultEntryOut])
async def list_user_vault_for_admin(
    user_id: str,
    context: AdminContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> list[VaultEntryOut]:
    await _require_membership_user(session, context, user_id)
    rows = await UserVaultRepository(session, context).list_for_user(user_id)
    return [
        VaultEntryOut(name=row.name, kind=row.kind, updated_at=row.updated_at)  # type: ignore[arg-type]
        for row in rows
    ]


@router.put("/users/{user_id}/{name}", response_model=VaultEntryOut)
async def upsert_user_vault_for_admin(
    user_id: str,
    name: str,
    body: VaultUpsertIn,
    context: AdminContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> VaultEntryOut:
    await _require_membership_user(session, context, user_id)
    key = _validate_name(name)
    cipher = _cipher(settings)
    if isinstance(cipher, AwsKmsCipher):
        envelope = await cipher.aencrypt(body.value)
    else:
        envelope = await asyncio.to_thread(cipher.encrypt, body.value)
    row = await UserVaultRepository(session, context).upsert(
        user_id=user_id,
        name=key,
        kind=body.kind,
        encrypted_value=envelope.ciphertext,
        key_version=envelope.key_version,
    )
    return VaultEntryOut(name=row.name, kind=row.kind, updated_at=row.updated_at)  # type: ignore[arg-type]


@router.delete("/users/{user_id}/{name}", status_code=204)
async def delete_user_vault_for_admin(
    user_id: str,
    name: str,
    context: AdminContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> Response:
    await _require_membership_user(session, context, user_id)
    key = _validate_name(name)
    deleted = await UserVaultRepository(session, context).delete_for_user(user_id, key)
    if not deleted:
        raise HTTPException(status_code=404, detail="Vault entry not found")
    return Response(status_code=204)
