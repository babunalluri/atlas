"""Keycloak Admin API provisioning for Atlas staff identity.

Passwords are sent only to Keycloak. They are never persisted in Atlas Postgres
and must not be written to logs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.settings import Settings, get_settings
from app.db.email_uniqueness import EMAIL_ALREADY_IN_USE
from app.db.models import Role

MIN_PASSWORD_LENGTH = 8


class IdentityProvisionError(RuntimeError):
    """Raised when identity provisioning fails."""

    status_code = 502


class IdentityNotConfiguredError(IdentityProvisionError):
    status_code = 503


class IdentityUserExistsError(IdentityProvisionError):
    status_code = 409


class IdentityForbiddenError(IdentityProvisionError):
    status_code = 403


class IdentityPasswordError(IdentityProvisionError):
    status_code = 400


class IdentityNotFoundError(IdentityProvisionError):
    status_code = 404


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
    return user_id.startswith("pending:") or user_id.startswith("invite:")


def org_role_for_atlas_role(role: Role) -> str:
    if role == Role.tenant_admin:
        return "org:admin"
    return "org:member"


def validate_password(password: str, *, confirm: str | None = None) -> str:
    value = password.strip() if password else ""
    if len(value) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
        )
    if confirm is not None and value != confirm:
        raise ValueError("Passwords do not match")
    return value


def require_password(password: str, *, confirm: str | None = None) -> str:
    try:
        return validate_password(password, confirm=confirm)
    except ValueError as exc:
        raise IdentityPasswordError(str(exc)) from exc


def _truthy_attr(values: Any) -> bool:
    if values is None:
        return False
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return False
    return any(str(item).strip().lower() in {"true", "1", "yes"} for item in values)


def _first_attr(values: Any) -> str | None:
    if isinstance(values, str) and values.strip():
        return values.strip()
    if isinstance(values, list):
        for item in values:
            if isinstance(item, str) and item.strip():
                return item.strip()
    return None


def _names_from_display(display_name: str, *, fallback: str) -> tuple[str, str]:
    """Split a display name. Keycloak requires lastName for password-grant login."""
    cleaned = display_name.strip()
    parts = cleaned.split(None, 1) if cleaned else []
    first_name = parts[0] if parts else (fallback.strip() or "User")
    last_name = parts[1] if len(parts) > 1 else first_name
    return first_name, last_name


def _heal_required_names(user: dict[str, Any]) -> None:
    first = str(user.get("firstName") or "").strip()
    last = str(user.get("lastName") or "").strip()
    if first and not last:
        user["lastName"] = first
    elif last and not first:
        user["firstName"] = last
    user["firstName"] = str(user.get("firstName") or "").strip()
    user["lastName"] = str(user.get("lastName") or "").strip()


def humanize_identity_error(message: str) -> str:
    """Map IdP error codes to admin-facing copy (never mention the IdP by name)."""
    text = message.strip()
    lowered = text.lower()
    if "error-user-attribute-read-only" in lowered:
        return (
            "That sign-in field cannot be changed. You can still update the "
            "display name or password."
        )
    return text


def _keycloak_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, str) and payload.strip():
        return humanize_identity_error(payload)
    if not isinstance(payload, dict):
        return fallback
    for key in ("errorMessage", "error_description", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return humanize_identity_error(value)
    return fallback


def _id_from_location(response: httpx.Response) -> str | None:
    location = response.headers.get("Location") or response.headers.get("location")
    if not location:
        return None
    return urlparse(location).path.rstrip("/").split("/")[-1] or None


class IdentityAdminClient:
    """Provision Keycloak users/groups via the Admin REST API."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._http = http
        self._token: str | None = None
        self._token_expires_at = 0.0

    def configured(self) -> bool:
        if self._http is not None:
            return True
        return bool(
            self._admin_origin()
            and self.settings.keycloak_admin_username.strip()
            and self.settings.keycloak_admin_password.get_secret_value()
        )

    def _admin_origin(self) -> str:
        explicit = self.settings.keycloak_admin_url.strip().rstrip("/")
        if explicit:
            return explicit
        for candidate in (self.settings.auth_jwks_url, self.settings.auth_issuer):
            if candidate and "/realms/" in candidate:
                return candidate.split("/realms/", 1)[0].rstrip("/")
        return ""

    def _realm(self) -> str:
        configured = self.settings.keycloak_realm.strip()
        if configured:
            return configured
        issuer = self.settings.auth_issuer.rstrip("/")
        if "/realms/" in issuer:
            return issuer.split("/realms/", 1)[1].split("/", 1)[0] or "atlas"
        return "atlas"

    def _admin_base(self) -> str:
        return f"{self._admin_origin()}/admin/realms/{self._realm()}"

    def _require_configured(self) -> None:
        if not self.configured():
            raise IdentityNotConfiguredError(
                "Keycloak admin is not configured "
                "(set KEYCLOAK_ADMIN_URL, KEYCLOAK_ADMIN, KEYCLOAK_ADMIN_PASSWORD)"
            )

    async def _http_client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30.0)
        return self._http

    async def _admin_token(self) -> str:
        now = time.monotonic()
        if self._token and now < self._token_expires_at:
            return self._token
        origin = self._admin_origin()
        token_url = (
            f"{origin}/realms/{self.settings.keycloak_admin_realm.strip() or 'master'}"
            "/protocol/openid-connect/token"
        )
        http = await self._http_client()
        try:
            response = await http.post(
                token_url,
                data={
                    "grant_type": "password",
                    "client_id": "admin-cli",
                    "username": self.settings.keycloak_admin_username.strip(),
                    "password": self.settings.keycloak_admin_password.get_secret_value(),
                },
            )
        except httpx.HTTPError as exc:
            raise IdentityProvisionError(
                "Could not reach Keycloak admin token endpoint"
            ) from exc
        if response.status_code >= 400:
            raise IdentityProvisionError(
                _keycloak_message(self._safe_json(response), "Keycloak admin login failed")
            )
        payload = self._safe_json(response)
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise IdentityProvisionError("Keycloak admin token was missing")
        expires_in = 60
        if isinstance(payload, dict) and isinstance(payload.get("expires_in"), int):
            expires_in = max(10, payload["expires_in"])
        self._token = token
        self._token_expires_at = now + expires_in - 10
        return token

    @staticmethod
    def _safe_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return None

    async def _admin_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        expected: set[int] | None = None,
    ) -> httpx.Response:
        self._require_configured()
        token = await self._admin_token()
        http = await self._http_client()
        url = path if path.startswith("http") else f"{self._admin_base()}{path}"
        try:
            response = await http.request(
                method,
                url,
                params=params,
                json=json_body,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise IdentityProvisionError("Could not reach Keycloak admin API") from exc
        allowed = expected or {200, 201, 204}
        if response.status_code == 401:
            self._token = None
            self._token_expires_at = 0.0
            token = await self._admin_token()
            try:
                response = await http.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPError as exc:
                raise IdentityProvisionError("Could not reach Keycloak admin API") from exc
        if response.status_code not in allowed:
            message = _keycloak_message(
                self._safe_json(response), "Identity provider request failed"
            )
            lowered = message.lower()
            user_conflict = "/users" in url and (
                response.status_code == 409
                or "already exists" in lowered
                or "user exists" in lowered
            )
            if user_conflict:
                raise IdentityUserExistsError(EMAIL_ALREADY_IN_USE)
            if response.status_code == 400 and "password" in lowered:
                raise IdentityPasswordError(message)
            if response.status_code == 404:
                raise IdentityNotFoundError(message)
            raise IdentityProvisionError(message)
        return response

    async def _admin_json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._admin_request(method, path, **kwargs)
        if response.status_code == 204 or not response.content:
            return None
        return self._safe_json(response)

    async def ensure_org_group(self, organization_id: str) -> str:
        org_id = organization_id.strip()
        if not org_id or org_id == "dev":
            raise IdentityProvisionError(
                "Tenant is not linked to an organization — cannot provision users"
            )
        rows = await self._admin_json(
            "GET",
            "/groups",
            params={"search": org_id, "briefRepresentation": "false"},
            expected={200},
        )
        matches = rows if isinstance(rows, list) else []
        for row in matches:
            if isinstance(row, dict) and row.get("name") == org_id:
                group_id = row.get("id")
                if isinstance(group_id, str) and group_id:
                    attrs = (
                        row.get("attributes")
                        if isinstance(row.get("attributes"), dict)
                        else {}
                    )
                    current = (
                        _first_attr(attrs.get("org_id"))
                        if isinstance(attrs, dict)
                        else None
                    )
                    if current != org_id:
                        merged_attrs = dict(attrs) if isinstance(attrs, dict) else {}
                        merged_attrs["org_id"] = [org_id]
                        await self._admin_request(
                            "PUT",
                            f"/groups/{group_id}",
                            json_body={
                                "id": group_id,
                                "name": org_id,
                                "attributes": merged_attrs,
                            },
                            expected={204, 200},
                        )
                    return group_id
        created = await self._admin_request(
            "POST",
            "/groups",
            json_body={"name": org_id, "attributes": {"org_id": [org_id]}},
            expected={201, 204, 409},
        )
        if created.status_code != 409:
            group_id = _id_from_location(created)
            if group_id:
                return group_id
        refreshed = await self._admin_json(
            "GET",
            "/groups",
            params={"search": org_id, "briefRepresentation": "false"},
            expected={200},
        )
        for row in refreshed if isinstance(refreshed, list) else []:
            if (
                isinstance(row, dict)
                and row.get("name") == org_id
                and isinstance(row.get("id"), str)
            ):
                return row["id"]
        raise IdentityProvisionError(f"Keycloak group `{org_id}` could not be created")

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        if not user_id.strip() or is_pending_user_id(user_id) or user_id.startswith("local:"):
            return None
        try:
            payload = await self._admin_json("GET", f"/users/{user_id.strip()}", expected={200})
        except IdentityNotFoundError:
            return None
        return payload if isinstance(payload, dict) else None

    async def find_user_by_email(self, email: str) -> dict[str, Any] | None:
        normalized = email.strip().lower()
        if "@" not in normalized:
            return None
        for params in (
            {"email": normalized, "exact": "true"},
            {"username": normalized, "exact": "true"},
        ):
            rows = await self._admin_json("GET", "/users", params=params, expected={200})
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                username = str(row.get("username") or "").strip().lower()
                row_email = str(row.get("email") or "").strip().lower()
                if username == normalized or row_email == normalized:
                    return row
        return None

    async def is_platform_admin_user(self, user: dict[str, Any]) -> bool:
        attrs = user.get("attributes") if isinstance(user.get("attributes"), dict) else {}
        if isinstance(attrs, dict) and _truthy_attr(attrs.get("platform_admin")):
            return True
        user_id = user.get("id")
        if not isinstance(user_id, str) or not user_id:
            return False
        mappings = await self._admin_json(
            "GET",
            f"/users/{user_id}/role-mappings/realm",
            expected={200},
        )
        if not isinstance(mappings, list):
            return False
        return any(
            isinstance(row, dict) and row.get("name") == "platform_admin" for row in mappings
        )

    async def _require_not_platform_admin(self, user: dict[str, Any]) -> None:
        if await self.is_platform_admin_user(user):
            raise IdentityForbiddenError(
                "Cannot edit or reset a platform administrator"
            )

    async def create_org_user(
        self,
        *,
        email: str,
        display_name: str,
        role: Role,
        organization_id: str,
        password: str,
        email_verified: bool = True,
    ) -> ProvisionedIdentity:
        normalized = email.strip().lower()
        if "@" not in normalized:
            raise IdentityPasswordError("A valid email is required")
        secret = require_password(password)
        existing = await self.find_user_by_email(normalized)
        if existing is not None:
            raise IdentityUserExistsError(EMAIL_ALREADY_IN_USE)
        group_id = await self.ensure_org_group(organization_id)
        first_name, last_name = _names_from_display(
            display_name, fallback=normalized.split("@", 1)[0]
        )
        created = await self._admin_request(
            "POST",
            "/users",
            json_body={
                "username": normalized,
                "email": normalized,
                "enabled": True,
                "emailVerified": email_verified,
                "firstName": first_name,
                "lastName": last_name,
                "attributes": {
                    "org_id": [organization_id.strip()],
                    "org_role": [org_role_for_atlas_role(role)],
                    "platform_admin": ["false"],
                },
            },
            expected={201, 204},
        )
        user_id = _id_from_location(created)
        if not user_id:
            found = await self.find_user_by_email(normalized)
            user_id = str(found.get("id") or "") if found else ""
        if not user_id:
            raise IdentityProvisionError("Keycloak user was created without an id")
        try:
            await self._admin_request(
                "PUT",
                f"/users/{user_id}/reset-password",
                json_body={"type": "password", "temporary": False, "value": secret},
                expected={204, 200},
            )
            await self._admin_request(
                "PUT",
                f"/users/{user_id}/groups/{group_id}",
                expected={204, 200},
            )
        except Exception:
            await self.delete_user(user_id)
            raise
        return ProvisionedIdentity(
            user_id=user_id,
            email=normalized,
            invite_pending=False,
            detail="Keycloak user created",
        )

    async def provision_org_owner(
        self,
        *,
        email: str,
        display_name: str,
        organization_id: str,
        password: str,
    ) -> ProvisionedIdentity:
        return await self.create_org_user(
            email=email,
            display_name=display_name,
            role=Role.tenant_admin,
            organization_id=organization_id,
            password=password,
        )

    async def delete_user(self, user_id: str) -> None:
        if not user_id.strip():
            return
        try:
            await self._admin_request(
                "DELETE",
                f"/users/{user_id.strip()}",
                expected={204, 200, 404},
            )
        except IdentityNotFoundError:
            return

    async def set_password(self, user_id: str, password: str) -> None:
        secret = require_password(password)
        user = await self.get_user(user_id)
        if user is None:
            raise IdentityNotFoundError("Identity user not found")
        await self._require_not_platform_admin(user)
        keycloak_id = str(user.get("id") or user_id)
        await self._admin_request(
            "PUT",
            f"/users/{keycloak_id}/reset-password",
            json_body={"type": "password", "temporary": False, "value": secret},
            expected={204, 200},
        )

    async def update_org_user(
        self,
        user_id: str,
        *,
        email: str | None = None,
        display_name: str | None = None,
        role: Role | None = None,
        password: str | None = None,
    ) -> dict[str, Any]:
        """Update profile, org_role, and optional password.

        Passwords are sent only to the IdP reset-password endpoint. Username is
        never rewritten on email change — the realm keeps username immutable.
        Email/username collisions 409 when another user already owns that address.
        """
        user = await self.get_user(user_id)
        if user is None:
            raise IdentityNotFoundError("Identity user not found")
        await self._require_not_platform_admin(user)
        keycloak_id = str(user.get("id") or user_id)
        secret = require_password(password) if password else None

        if email is not None:
            normalized = email.strip().lower()
            if "@" not in normalized:
                raise IdentityPasswordError("A valid email is required")
            current_email = str(user.get("email") or "").strip().lower()
            current_username = str(user.get("username") or "").strip().lower()
            if normalized not in {current_email, current_username}:
                existing = await self.find_user_by_email(normalized)
                if existing is not None and str(existing.get("id") or "") != keycloak_id:
                    raise IdentityUserExistsError(EMAIL_ALREADY_IN_USE)
                user["email"] = normalized
                user["emailVerified"] = True

        if display_name is not None:
            fallback = str(user.get("username") or user.get("email") or "user")
            first_name, last_name = _names_from_display(
                display_name, fallback=fallback
            )
            user["firstName"] = first_name
            user["lastName"] = last_name

        _heal_required_names(user)

        if role is not None:
            attrs = (
                dict(user["attributes"])
                if isinstance(user.get("attributes"), dict)
                else {}
            )
            attrs["org_role"] = [org_role_for_atlas_role(role)]
            user["attributes"] = attrs

        profile_changed = (
            email is not None or display_name is not None or role is not None
        )
        profile_error: IdentityProvisionError | None = None
        if profile_changed:
            body: dict[str, Any] = {
                "id": keycloak_id,
                "username": user.get("username"),
                "email": user.get("email"),
                "enabled": user.get("enabled", True),
                "emailVerified": user.get("emailVerified", True),
                "firstName": user.get("firstName") or "",
                "lastName": user.get("lastName") or "",
            }
            attrs = user.get("attributes")
            if isinstance(attrs, dict) and attrs:
                body["attributes"] = attrs
            try:
                await self._admin_request(
                    "PUT",
                    f"/users/{keycloak_id}",
                    json_body=body,
                    expected={204, 200},
                )
            except IdentityProvisionError as exc:
                profile_error = exc

        if secret:
            await self._admin_request(
                "PUT",
                f"/users/{keycloak_id}/reset-password",
                json_body={"type": "password", "temporary": False, "value": secret},
                expected={204, 200},
            )
        if profile_error:
            raise profile_error
        return user

    async def change_password(
        self,
        *,
        user_id: str,
        username: str,
        current_password: str,
        new_password: str,
    ) -> None:
        secret = require_password(new_password)
        if not current_password:
            raise IdentityPasswordError("Current password is required")
        user = await self.get_user(user_id)
        if user is None:
            raise IdentityNotFoundError("Identity user not found")
        login_name = (
            str(user.get("username") or "").strip()
            or username.strip()
            or str(user.get("email") or "").strip()
        )
        if not await self._verify_current_password(login_name, current_password):
            raise IdentityPasswordError("Current password is incorrect")
        keycloak_id = str(user.get("id") or user_id)
        await self._admin_request(
            "PUT",
            f"/users/{keycloak_id}/reset-password",
            json_body={"type": "password", "temporary": False, "value": secret},
            expected={204, 200},
        )

    async def _verify_current_password(self, username: str, password: str) -> bool:
        origin = self._admin_origin()
        client_id = (
            self.settings.keycloak_client_id.strip()
            or self.settings.auth_audience
            or "atlas-web"
        )
        client_secret = self.settings.keycloak_client_secret.get_secret_value()
        token_url = f"{origin}/realms/{self._realm()}/protocol/openid-connect/token"
        data = {
            "grant_type": "password",
            "client_id": client_id,
            "username": username,
            "password": password,
        }
        if client_secret:
            data["client_secret"] = client_secret
        http = await self._http_client()
        try:
            response = await http.post(token_url, data=data)
        except httpx.HTTPError as exc:
            raise IdentityProvisionError("Could not verify the current password") from exc
        if response.status_code == 200:
            return True
        if response.status_code in {400, 401}:
            return False
        raise IdentityProvisionError(
            _keycloak_message(self._safe_json(response), "Could not verify the current password")
        )

    async def provision_pending_invite(
        self,
        *,
        email: str,
        display_name: str,
        role: Role,
        organization_id: str,
        inviter_user_id: str,
        redirect_url: str,
    ) -> ProvisionedIdentity:
        """Unused primary path. Kept for IDENTITY_INVITE_ENABLED."""
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

    async def provision_tenant_user(
        self,
        *,
        email: str,
        display_name: str,
        role: Role,
        organization_id: str,
        inviter_user_id: str,
        redirect_url: str,
        password: str | None = None,
    ) -> ProvisionedIdentity:
        if password:
            return await self.create_org_user(
                email=email,
                display_name=display_name,
                role=role,
                organization_id=organization_id,
                password=password,
            )
        if self.settings.identity_invite_enabled:
            return await self.provision_pending_invite(
                email=email,
                display_name=display_name,
                role=role,
                organization_id=organization_id,
                inviter_user_id=inviter_user_id,
                redirect_url=redirect_url,
            )
        raise IdentityPasswordError("Password is required")

    async def create_dev_sign_in_url(
        self, user_id: str, *, organization_id: str
    ) -> str | None:
        del user_id, organization_id
        return None

    @staticmethod
    def primary_email(profile: dict) -> str | None:
        email = profile.get("email")
        if isinstance(email, str) and "@" in email:
            return email.strip().lower()
        return None
