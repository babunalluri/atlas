"""Local identity provisioning for Atlas (OIDC IdP is Keycloak — no Clerk).

Creates pending memberships that bind to the real IdP ``sub`` on first sign-in
(by email). Operators create users in Keycloak and assign the org group that
matches ``tenants.auth_org_id``.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.settings import Settings, get_settings
from app.db.models import Role


class IdentityProvisionError(RuntimeError):
    """Raised when provisioning input is invalid."""


@dataclass(slots=True)
class ProvisionedIdentity:
    user_id: str
    email: str
    invite_pending: bool
    detail: str = ""
    temporary_password: str | None = None
    sign_in_url: str | None = None


def pending_user_id(email: str) -> str:
    return f"pending:{email.strip().lower()}"


def is_pending_user_id(user_id: str) -> bool:
    return user_id.startswith("pending:")


class IdentityAdminClient:
    """Atlas-side invite helper. Does not call any SaaS identity Admin API."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def configured(self) -> bool:
        """Always true — invites only need Atlas DB + Keycloak operator steps."""
        return True

    async def provision_tenant_user(
        self,
        *,
        email: str,
        display_name: str,
        role: Role,
        organization_id: str,
        inviter_user_id: str,
        redirect_url: str,
    ) -> ProvisionedIdentity:
        del display_name, role, inviter_user_id, redirect_url
        org_id = organization_id.strip()
        if not org_id or org_id == "dev":
            raise IdentityProvisionError(
                "Tenant is not linked to an organization — cannot invite users"
            )
        normalized = email.strip().lower()
        if "@" not in normalized:
            raise IdentityProvisionError("A valid email is required")
        return ProvisionedIdentity(
            user_id=pending_user_id(normalized),
            email=normalized,
            invite_pending=True,
            detail=(
                "Pending Atlas membership created. In Keycloak, create this user "
                f"(or enable registration) and add them to group/org `{org_id}`. "
                "Set user attributes org_id / org_role as needed. "
                "Atlas binds the membership on first sign-in by email."
            ),
        )

    async def create_dev_sign_in_url(
        self, user_id: str, *, organization_id: str
    ) -> str | None:
        del user_id, organization_id
        return None

    async def get_user(self, user_id: str) -> dict | None:
        del user_id
        return None

    @staticmethod
    def primary_email(profile: dict) -> str | None:
        del profile
        return None
