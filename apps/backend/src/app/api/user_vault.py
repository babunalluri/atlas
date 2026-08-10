"""Authenticated org-user secrets and variables (write-only values)."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.core.settings import Settings, get_settings
from app.credentials.provider import AwsKmsCipher, LocalFernetCipher
from app.db.models import Role
from app.db.repositories import UserVaultRepository
from app.db.session import tenant_session
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/api/me/vault", tags=["user-vault"])
MeContext = Annotated[
    TenantContext,
    Depends(
        require_roles(Role.platform_admin, Role.tenant_admin, Role.end_user)
    ),
]

_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")


class VaultEntryOut(BaseModel):
    name: str
    kind: Literal["secret", "variable"]
    updated_at: datetime


class VaultUpsertIn(BaseModel):
    value: str = Field(min_length=1, max_length=16_384)
    kind: Literal["secret", "variable"] = "secret"

    @field_validator("value")
    @classmethod
    def strip_value(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Value cannot be empty")
        return cleaned


def _cipher(settings: Settings):
    if settings.aws_kms_key_id:
        return AwsKmsCipher(settings.aws_kms_key_id, settings.aws_region)
    return LocalFernetCipher(
        settings.encryption_key.get_secret_value(),
        settings.encryption_key_version,
        previous_keys=settings.encryption_previous_keys,
    )


def _validate_name(name: str) -> str:
    if not _NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail="Name must match ^[a-zA-Z][a-zA-Z0-9_]{0,63}$",
        )
    return name


@router.get("", response_model=list[VaultEntryOut])
async def list_vault(
    context: MeContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> list[VaultEntryOut]:
    rows = await UserVaultRepository(session, context).list_for_user(context.user_id)
    return [
        VaultEntryOut(name=row.name, kind=row.kind, updated_at=row.updated_at)  # type: ignore[arg-type]
        for row in rows
    ]


@router.put("/{name}", response_model=VaultEntryOut)
async def upsert_vault_entry(
    name: str,
    body: VaultUpsertIn,
    context: MeContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> VaultEntryOut:
    key = _validate_name(name)
    cipher = _cipher(settings)
    if isinstance(cipher, AwsKmsCipher):
        envelope = await cipher.aencrypt(body.value)
    else:
        envelope = await asyncio.to_thread(cipher.encrypt, body.value)
    row = await UserVaultRepository(session, context).upsert(
        user_id=context.user_id,
        name=key,
        kind=body.kind,
        encrypted_value=envelope.ciphertext,
        key_version=envelope.key_version,
    )
    return VaultEntryOut(name=row.name, kind=row.kind, updated_at=row.updated_at)  # type: ignore[arg-type]


@router.delete("/{name}", status_code=204)
async def delete_vault_entry(
    name: str,
    context: MeContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> Response:
    key = _validate_name(name)
    deleted = await UserVaultRepository(session, context).delete_for_user(
        context.user_id, key
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Vault entry not found")
    return Response(status_code=204)
