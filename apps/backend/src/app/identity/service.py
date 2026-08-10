"""Generic per-tenant end-user identity (OTP bind for public surfaces)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.factory import AgentFactoryService
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.db.models import EndUser
from app.db.repositories import (
    CredentialRepository,
    EndUserRepository,
    EndUserSessionBindRepository,
    SessionRepository,
    VerificationChallengeRepository,
)
from app.email.resend import ResendError, send_resend_email
from app.tenancy.context import TenantContext

logger = get_logger(__name__)


@dataclass(slots=True)
class ChallengeResult:
    email: str
    expires_at: datetime
    debug_code: str | None = None


@dataclass(slots=True)
class VerifyResult:
    end_user_id: str
    email: str
    display_name: str
    metadata: dict[str, Any]


def _hash_code(code: str, *, tenant_id: str, email: str) -> str:
    material = f"{tenant_id}:{email.strip().lower()}:{code.strip()}"
    return hashlib.sha256(material.encode()).hexdigest()


def _normalize_email(email: str) -> str:
    return email.strip().lower()


class IdentityService:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context
        self.end_users = EndUserRepository(session, context)
        self.challenges = VerificationChallengeRepository(session, context)
        self.binds = EndUserSessionBindRepository(session, context)
        self.sessions = SessionRepository(session, context)

    async def request_challenge(
        self,
        *,
        email: str,
        external_session_id: str,
        guest_user_id: str,
    ) -> ChallengeResult:
        normalized = _normalize_email(email)
        if "@" not in normalized or len(normalized) > 320:
            raise ValueError("Invalid email address")
        if not external_session_id.strip():
            raise ValueError("session_id is required")

        code = f"{secrets.randbelow(1_000_000):06d}"
        code_hash = _hash_code(
            code, tenant_id=str(self.context.tenant_id), email=normalized
        )
        challenge = await self.challenges.create_challenge(
            email=normalized,
            code_hash=code_hash,
            external_session_id=external_session_id.strip(),
            guest_user_id=guest_user_id,
        )
        await self._send_otp_email(normalized, code)
        settings = get_settings()
        debug_code = code if settings.is_development else None
        return ChallengeResult(
            email=normalized,
            expires_at=challenge.expires_at,
            debug_code=debug_code,
        )

    async def verify_challenge(
        self,
        *,
        email: str,
        code: str,
        external_session_id: str,
        guest_user_id: str,
    ) -> VerifyResult:
        normalized = _normalize_email(email)
        challenge = await self.challenges.get_open(
            email=normalized, external_session_id=external_session_id.strip()
        )
        if challenge is None:
            raise LookupError("No active verification code for this session")
        if challenge.guest_user_id != guest_user_id:
            raise PermissionError("Verification belongs to another guest")
        if challenge.expires_at < datetime.now(UTC):
            raise ValueError("Verification code expired")
        if challenge.attempt_count >= 5:
            raise ValueError("Too many attempts — request a new code")

        expected = _hash_code(
            code, tenant_id=str(self.context.tenant_id), email=normalized
        )
        challenge.attempt_count += 1
        if not hmac.compare_digest(expected, challenge.code_hash):
            await self.session.flush()
            raise ValueError("Invalid verification code")

        challenge.consumed_at = datetime.now(UTC)
        end_user = await self.end_users.get_or_create(
            email=normalized, mark_verified=True
        )
        if not end_user.is_active:
            raise PermissionError("This user is disabled")

        await self.binds.upsert(
            external_session_id=external_session_id.strip(),
            guest_user_id=guest_user_id,
            end_user_id=end_user.id,
        )
        try:
            await self.sessions.bind_verified_end_user(
                external_session_id=external_session_id.strip(),
                end_user_id=end_user.id,
                guest_user_id=guest_user_id,
            )
        except PermissionError:
            raise
        await self.session.flush()
        return VerifyResult(
            end_user_id=str(end_user.id),
            email=end_user.email,
            display_name=end_user.display_name,
            metadata=dict(end_user.user_metadata or {}),
        )

    async def resolve_for_session(
        self, *, external_session_id: str, guest_user_id: str
    ) -> EndUser | None:
        session_row = await self.sessions.get_by_external(external_session_id)
        end_user_id = None
        if session_row is not None and session_row.user_id == guest_user_id:
            end_user_id = session_row.verified_end_user_id
        if end_user_id is None:
            bind = await self.binds.get_for_session(
                external_session_id=external_session_id,
                guest_user_id=guest_user_id,
            )
            if bind is not None:
                end_user_id = bind.end_user_id
                if session_row is not None and session_row.verified_end_user_id is None:
                    session_row.verified_end_user_id = end_user_id
        if end_user_id is None:
            return None
        user = await self.end_users.get(end_user_id)
        if user is None or not user.is_active:
            return None
        return user

    async def ensure_email_identity(self, email: str) -> EndUser:
        """Claim identity from inbound email (mailbox control)."""
        return await self.end_users.get_or_create(
            email=_normalize_email(email), mark_verified=True
        )

    async def _send_otp_email(self, email: str, code: str) -> None:
        settings = get_settings()
        api_key = ""
        credential = await CredentialRepository(
            self.session, self.context
        ).get_for_provider("resend")
        if credential is not None:
            api_key = AgentFactoryService._decrypt(  # noqa: SLF001
                credential.encrypted_value, credential.key_version
            )
        else:
            api_key = settings.resend_api_key.get_secret_value().strip()
        if not api_key:
            if settings.is_development:
                logger.info("identity_otp_dev_fallback", email=email, code=code)
                return
            raise RuntimeError(
                "Email delivery is not configured (add a resend credential)"
            )
        domain = (settings.email_inbound_domain or "localhost").strip() or "localhost"
        from_address = f"noreply@{domain}"
        try:
            await send_resend_email(
                api_key,
                from_address=from_address,
                to_address=email,
                subject="Your verification code",
                text=(
                    f"Your verification code is {code}.\n\n"
                    "It expires in 15 minutes. If you did not request this, ignore this email."
                ),
            )
        except ResendError:
            if settings.is_development:
                logger.warning("identity_otp_send_failed_dev", email=email, code=code)
                return
            raise
