import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import CredentialCreateIn, CredentialOut
from app.auth.dependencies import require_roles
from app.core.settings import Settings, get_settings
from app.credentials.provider import AwsKmsCipher, CredentialCipher, LocalFernetCipher
from app.db.models import Role, TenantCredential
from app.db.repositories import CredentialRepository
from app.db.session import tenant_session
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/credentials", tags=["admin-credentials"])


def _cipher(settings: Settings) -> CredentialCipher:
    if settings.aws_kms_key_id:
        return AwsKmsCipher(settings.aws_kms_key_id, settings.aws_region)
    return LocalFernetCipher(
        settings.encryption_key.get_secret_value(),
        settings.encryption_key_version,
        previous_keys=settings.encryption_previous_keys,
    )


def _out(row: TenantCredential) -> CredentialOut:
    return CredentialOut(
        id=row.id,
        name=row.name,
        provider=row.provider,
        key_version=row.key_version,
        created_at=row.created_at,
    )


@router.get("", response_model=list[CredentialOut])
async def list_credentials(
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> list[CredentialOut]:
    rows = await CredentialRepository(session, context).list()
    return [_out(row) for row in rows]


@router.post("", response_model=CredentialOut, status_code=201)
async def create_credential(
    body: CredentialCreateIn,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CredentialOut:
    cipher = _cipher(settings)
    if isinstance(cipher, AwsKmsCipher):
        envelope = await cipher.aencrypt(body.value)
    else:
        envelope = await asyncio.to_thread(cipher.encrypt, body.value)
    row = await CredentialRepository(session, context).create(
        name=body.name,
        provider=body.provider,
        encrypted_value=envelope.ciphertext,
        key_version=envelope.key_version,
    )
    return _out(row)


@router.delete("/{credential_id}", status_code=204)
async def delete_credential(
    credential_id: uuid.UUID,
    context: Annotated[
        TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
    ],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> None:
    try:
        deleted = await CredentialRepository(session, context).delete(credential_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Credential not found")
