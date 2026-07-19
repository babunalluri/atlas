import base64
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, text

from app.db.models import Role, ServiceAccount, Tenant
from app.db.session import SessionFactory
from app.tenancy.context import TenantContext

PAT_PREFIX = "agno_pat_"


@dataclass(frozen=True, slots=True)
class MintedServiceAccount:
    token: str
    token_prefix: str
    token_hash: str


def hash_service_account_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_service_account_token(tenant_id: uuid.UUID) -> MintedServiceAccount:
    tenant_hint = base64.urlsafe_b64encode(tenant_id.bytes).decode("ascii").rstrip("=")
    token = f"{PAT_PREFIX}{tenant_hint}_{secrets.token_urlsafe(32)}"
    return MintedServiceAccount(
        token=token,
        token_prefix=f"{PAT_PREFIX}{tenant_hint[:8]}…",
        token_hash=hash_service_account_token(token),
    )


def _tenant_id_from_token(token: str) -> uuid.UUID:
    if not token.startswith(PAT_PREFIX):
        raise ValueError("Not a service account token")
    parts = token[len(PAT_PREFIX) :].split("_", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("Malformed service account token")
    tenant_hint = parts[0] + ("=" * (-len(parts[0]) % 4))
    raw = base64.urlsafe_b64decode(tenant_hint.encode("ascii"))
    if len(raw) != 16:
        raise ValueError("Malformed tenant hint")
    return uuid.UUID(bytes=raw)


async def authenticate_service_account(token: str) -> TenantContext | None:
    try:
        tenant_id = _tenant_id_from_token(token)
    except (ValueError, TypeError):
        return None

    token_hash = hash_service_account_token(token)
    now = datetime.now(UTC)
    async with SessionFactory() as session:
        if session.bind and session.bind.dialect.name == "postgresql":
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
        account = await session.scalar(
            select(ServiceAccount).where(
                ServiceAccount.tenant_id == tenant_id,
                ServiceAccount.token_hash == token_hash,
                ServiceAccount.revoked_at.is_(None),
            )
        )
        if account is None or (account.expires_at is not None and account.expires_at <= now):
            return None
        tenant = await session.scalar(
            select(Tenant).where(Tenant.id == tenant_id, Tenant.is_active.is_(True))
        )
        if tenant is None:
            return None
        account.last_used_at = now
        await session.commit()
        return TenantContext(
            tenant_id=tenant.id,
            user_id=f"sa:{account.id}",
            role=Role.end_user,
            clerk_org_id=tenant.clerk_org_id,
            scopes=tuple(account.scopes),
            principal_type="service_account",
        )
