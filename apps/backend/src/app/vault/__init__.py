"""Per-user vault helpers for tool settings and Agno session_state."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings, get_settings
from app.credentials.provider import AwsKmsCipher, EncryptedEnvelope, LocalFernetCipher
from app.db.repositories import UserVaultRepository
from app.tenancy.context import TenantContext

TOKEN_CANDIDATE_NAMES = ("user_token", "bearer_token", "api_key")


def _cipher(settings: Settings):
    if settings.aws_kms_key_id:
        return AwsKmsCipher(settings.aws_kms_key_id, settings.aws_region)
    return LocalFernetCipher(
        settings.encryption_key.get_secret_value(),
        settings.encryption_key_version,
        previous_keys=settings.encryption_previous_keys,
    )


def decrypt_vault_value(encrypted_value: str, key_version: str, settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    envelope = EncryptedEnvelope(encrypted_value, key_version)
    cipher = _cipher(cfg)
    if isinstance(cipher, AwsKmsCipher):
        # Callers that need async KMS should use aload_user_vault_map.
        raise RuntimeError("Use aload_user_vault_map for KMS-backed vault decryption")
    return cipher.decrypt(envelope)


async def aload_user_vault_map(
    session: AsyncSession,
    context: TenantContext,
    *,
    settings: Settings | None = None,
) -> dict[str, str]:
    """Decrypt the caller's vault into a plain name→value map."""
    cfg = settings or get_settings()
    rows = await UserVaultRepository(session, context).list_for_user(context.user_id)
    if not rows:
        return {}
    cipher = _cipher(cfg)
    out: dict[str, str] = {}
    for row in rows:
        envelope = EncryptedEnvelope(row.encrypted_value, row.key_version)
        if isinstance(cipher, AwsKmsCipher):
            out[row.name] = await cipher.adecrypt(envelope)
        else:
            out[row.name] = await asyncio.to_thread(cipher.decrypt, envelope)
    return out


def pick_user_token(vault: dict[str, str]) -> str | None:
    for name in TOKEN_CANDIDATE_NAMES:
        value = (vault.get(name) or "").strip()
        if value:
            return value
    return None


def merge_user_vault_settings(
    settings: dict[str, Any],
    vault: dict[str, str],
) -> dict[str, Any]:
    """Overlay user vault onto tool settings (user wins)."""
    merged = dict(settings)
    for key, value in vault.items():
        if value is None:
            continue
        merged[key] = value
    return merged


def session_state_for_user(
    context: TenantContext,
    vault: dict[str, str],
) -> dict[str, Any] | None:
    """Build Agno session_state for legacy _resolve_token(run_context) tools."""
    token = pick_user_token(vault)
    data: dict[str, Any] = {"userId": context.user_id}
    if token:
        data["user_token"] = token
    # Expose non-token vault keys under data for toolkit convenience.
    for key, value in vault.items():
        if key in TOKEN_CANDIDATE_NAMES:
            continue
        data[key] = value
    if not token and len(data) <= 1:
        return None
    return {
        "additional_information": {
            "data": data,
        }
    }
