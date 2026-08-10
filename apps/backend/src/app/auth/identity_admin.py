"""Provision org users via the identity Backend API (invite + membership)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.logging import get_logger
from app.core.settings import Settings, get_settings
from app.db.models import Role

logger = get_logger(__name__)

CLERK_API_BASE = "https://api.clerk.com/v1"


class IdentityProvisionError(RuntimeError):
    """Raised when the identity provider rejects provisioning."""


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


def atlas_role_to_org_role(role: Role) -> str:
    return "org:admin" if role == Role.tenant_admin else "org:member"


class IdentityAdminClient:
    """Thin Backend API client for org invites and memberships."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def _secret(self) -> str:
        return self.settings.clerk_secret_key.get_secret_value().strip()

    def configured(self) -> bool:
        secret = self._secret
        return bool(secret) and "replace_me" not in secret

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
        """Ensure email exists as a sign-in user AND as a member of the tenant org.

        Preferred path: create/link the account immediately (real user id).
        Fallback: org invitation email + pending Atlas bind until first sign-in.
        """
        if not self.configured():
            raise IdentityProvisionError(
                "Identity provider is not configured (set CLERK_SECRET_KEY)"
            )
        org_id = organization_id.strip()
        if not org_id or org_id == "dev":
            raise IdentityProvisionError(
                "Tenant is not linked to an organization — cannot invite users"
            )
        normalized = email.strip().lower()
        if "@" not in normalized:
            raise IdentityProvisionError("A valid email is required")

        org_role = atlas_role_to_org_role(role)
        existing = await self.find_user_by_email(normalized)
        if existing is not None:
            user_id = str(existing["id"])
            await self.ensure_organization_membership(
                organization_id=org_id,
                user_id=user_id,
                role=org_role,
            )
            temp_password = None
            sign_in_url = None
            if self.settings.is_development:
                temp_password = await self.ensure_dev_password(user_id)
                sign_in_url = await self.create_dev_sign_in_url(
                    user_id, organization_id=org_id
                )
            return ProvisionedIdentity(
                user_id=user_id,
                email=normalized,
                invite_pending=False,
                detail="Linked existing sign-in account to this organization",
                temporary_password=temp_password,
                sign_in_url=sign_in_url,
            )

        # Create the sign-in account first so Atlas always maps a real user id.
        try:
            created = await self.create_user(
                email=normalized, display_name=display_name
            )
            user_id = str(created["id"])
            await self.ensure_organization_membership(
                organization_id=org_id,
                user_id=user_id,
                role=org_role,
            )
            temp_password: str | None = None
            sign_in_url: str | None = None
            if self.settings.is_development:
                temp_password = self.settings.dev_user_password.get_secret_value()
                sign_in_url = await self.create_dev_sign_in_url(
                    user_id, organization_id=org_id
                )
            else:
                try:
                    await self.create_app_invitation(
                        email=normalized, redirect_url=redirect_url
                    )
                except IdentityProvisionError as exc:
                    logger.info(
                        "identity_app_invite_skipped_after_create",
                        email=normalized,
                        error=str(exc),
                    )
            return ProvisionedIdentity(
                user_id=user_id,
                email=normalized,
                invite_pending=False,
                detail=(
                    "Created sign-in account and added to organization"
                    + (
                        " (dev: one-click sign-in link available)"
                        if sign_in_url
                        else ""
                    )
                ),
                temporary_password=temp_password,
                sign_in_url=sign_in_url,
            )
        except IdentityProvisionError as create_exc:
            logger.warning(
                "identity_create_user_failed_falling_back_to_invite",
                email=normalized,
                error=str(create_exc),
            )

        # Fallback: classic org invite (user id appears after they accept).
        await self.create_organization_invitation(
            organization_id=org_id,
            email=normalized,
            role=org_role,
            inviter_user_id=inviter_user_id,
            redirect_url=redirect_url,
        )
        return ProvisionedIdentity(
            user_id=pending_user_id(normalized),
            email=normalized,
            invite_pending=True,
            detail="Invitation email sent; Atlas will bind their account on first sign-in",
        )

    async def create_user(self, *, email: str, display_name: str) -> dict[str, Any]:
        parts = [part for part in display_name.strip().split() if part]
        first = parts[0] if parts else email.split("@", 1)[0]
        last = " ".join(parts[1:]) if len(parts) > 1 else ""
        body: dict[str, Any] = {
            "email_address": [email.strip().lower()],
            "first_name": first[:255],
            "skip_password_checks": True,
            "skip_email_verification": True,
        }
        if last:
            body["last_name"] = last[:255]
        if self.settings.is_development:
            body["password"] = self.settings.dev_user_password.get_secret_value()
        else:
            body["skip_password_requirement"] = True
        data = await self._request("POST", "/users", json=body)
        if not isinstance(data, dict) or "id" not in data:
            raise IdentityProvisionError("Identity provider did not return a user id")
        return data

    async def ensure_dev_password(self, user_id: str) -> str:
        """Set the shared development password so email OTP can be skipped."""
        password = self.settings.dev_user_password.get_secret_value()
        await self._request(
            "PATCH",
            f"/users/{user_id}",
            json={
                "password": password,
                "skip_password_checks": True,
            },
        )
        return password

    async def create_dev_sign_in_url(
        self, user_id: str, *, organization_id: str | None = None
    ) -> str:
        """One-click sign-in URL for development (bypasses email OTP)."""
        body: dict[str, Any] = {
            "user_id": user_id,
            "expires_in_seconds": 60 * 60 * 12,
        }
        if organization_id and organization_id != "dev":
            body["org_id"] = organization_id
        data = await self._request("POST", "/sign_in_tokens", json=body)
        token = ""
        if isinstance(data, dict):
            token = str(data.get("token") or "")
        if not token:
            raise IdentityProvisionError("Sign-in token was not returned")
        # Prefer Atlas sign-in so the app consumes the ticket (not Clerk Account Portal).
        base = self.settings.app_public_url.rstrip("/")
        return f"{base}/sign-in?__clerk_ticket={token}"

    async def find_user_by_email(self, email: str) -> dict[str, Any] | None:
        data = await self._request(
            "GET",
            "/users",
            params={"email_address": [email.strip().lower()], "limit": 1},
        )
        if isinstance(data, list):
            return data[0] if data else None
        if isinstance(data, dict):
            rows = data.get("data")
            if isinstance(rows, list) and rows:
                return rows[0]
        return None

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        try:
            data = await self._request("GET", f"/users/{user_id}")
        except IdentityProvisionError as exc:
            if "not found" in str(exc).lower() or "404" in str(exc):
                return None
            raise
        return data if isinstance(data, dict) else None

    @staticmethod
    def primary_email(user: dict[str, Any]) -> str | None:
        addresses = user.get("email_addresses")
        if not isinstance(addresses, list):
            return None
        primary_id = user.get("primary_email_address_id")
        for item in addresses:
            if not isinstance(item, dict):
                continue
            if primary_id and item.get("id") == primary_id:
                email = item.get("email_address")
                if isinstance(email, str) and "@" in email:
                    return email.strip().lower()
        for item in addresses:
            if isinstance(item, dict):
                email = item.get("email_address")
                if isinstance(email, str) and "@" in email:
                    return email.strip().lower()
        return None

    async def ensure_organization_membership(
        self,
        *,
        organization_id: str,
        user_id: str,
        role: str,
    ) -> None:
        try:
            await self._request(
                "POST",
                f"/organizations/{organization_id}/memberships",
                json={"user_id": user_id, "role": role},
            )
        except IdentityProvisionError as exc:
            message = str(exc).lower()
            if "already" in message or "exists" in message or "duplicate" in message:
                await self.update_organization_membership_role(
                    organization_id=organization_id,
                    user_id=user_id,
                    role=role,
                )
                return
            raise

    async def update_organization_membership_role(
        self,
        *,
        organization_id: str,
        user_id: str,
        role: str,
    ) -> None:
        try:
            await self._request(
                "PATCH",
                f"/organizations/{organization_id}/memberships/{user_id}",
                json={"role": role},
            )
        except IdentityProvisionError as exc:
            message = str(exc).lower()
            if "not found" in message or "404" in message:
                await self._request(
                    "POST",
                    f"/organizations/{organization_id}/memberships",
                    json={"user_id": user_id, "role": role},
                )
                return
            raise

    async def remove_organization_membership(
        self, *, organization_id: str, user_id: str
    ) -> None:
        if is_pending_user_id(user_id):
            return
        try:
            await self._request(
                "DELETE",
                f"/organizations/{organization_id}/memberships/{user_id}",
            )
        except IdentityProvisionError as exc:
            message = str(exc).lower()
            if "not found" in message or "404" in message:
                return
            raise

    async def create_app_invitation(
        self, *, email: str, redirect_url: str
    ) -> dict[str, Any]:
        """Send an application invitation / first-access email."""
        return await self._request(
            "POST",
            "/invitations",
            json={
                "email_address": email.strip().lower(),
                "redirect_url": redirect_url,
                "notify": True,
            },
        )

    async def create_organization_invitation(
        self,
        *,
        organization_id: str,
        email: str,
        role: str,
        inviter_user_id: str,
        redirect_url: str,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "email_address": email.strip().lower(),
            "role": role,
            "redirect_url": redirect_url,
        }
        # Backend secret is enough; only pass inviter when it looks like a real
        # user in this org (platform admins acting on other tenants often are not).
        if (
            inviter_user_id
            and not inviter_user_id.startswith("sa:")
            and not inviter_user_id.startswith("pending:")
            and inviter_user_id.startswith("user_")
        ):
            body["inviter_user_id"] = inviter_user_id
        return await self._request(
            "POST",
            f"/organizations/{organization_id}/invitations",
            json=body,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        headers = {
            "Authorization": f"Bearer {self._secret}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(base_url=CLERK_API_BASE, timeout=20.0) as client:
            response = await client.request(
                method, path, headers=headers, json=json, params=params
            )
        if response.status_code >= 400:
            detail = response.text
            try:
                payload = response.json()
                errors = payload.get("errors")
                if isinstance(errors, list) and errors:
                    detail = "; ".join(
                        str(item.get("long_message") or item.get("message") or item)
                        for item in errors
                    )
                elif isinstance(payload.get("message"), str):
                    detail = payload["message"]
            except Exception:
                pass
            raise IdentityProvisionError(detail or f"Identity API {response.status_code}")
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()
