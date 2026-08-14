"""Keycloak Admin API provisioning (mocked HTTP — no live IdP)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.auth.identity_admin import (
    IdentityAdminClient,
    IdentityForbiddenError,
    IdentityPasswordError,
    IdentityProvisionError,
    IdentityUserExistsError,
    humanize_identity_error,
    validate_password,
)
from app.core.settings import Settings
from app.db.models import Role

OWNER_PASSWORD = "owner-pass-1"  # noqa: S105
MEMBER_PASSWORD = "member-pass-1"  # noqa: S105


def _admin_settings() -> Settings:
    return Settings(
        auth_disabled=False,
        database_url="sqlite+aiosqlite:///:memory:",
        credential_encryption_key="dev-only-change-me-please-32b",
        auth_issuer="http://keycloak.test/realms/atlas",
        keycloak_admin_url="http://keycloak.test",
        keycloak_admin_username="admin",
        keycloak_admin_password=SecretStr("admin"),
        keycloak_client_id="atlas-web",
        keycloak_client_secret=SecretStr("atlas-web-dev-secret-change-me"),
    )


class FakeKeycloak:
    def __init__(self) -> None:
        self.groups: dict[str, dict[str, Any]] = {}
        self.users: dict[str, dict[str, Any]] = {}
        self.passwords: dict[str, str] = {}
        self.group_members: dict[str, set[str]] = {}
        self.search_misses = 0
        self.seq = 0
        self.profile_put_error: str | None = None

    def _id(self, prefix: str) -> str:
        self.seq += 1
        return f"{prefix}-{self.seq}"

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        body: dict[str, Any] = {}
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = {}

        if path.endswith("/protocol/openid-connect/token"):
            data = dict(httpx.QueryParams(request.content.decode()))
            if data.get("client_id") == "admin-cli":
                return httpx.Response(200, json={"access_token": "admin-token", "expires_in": 60})
            username = data.get("username", "")
            user = next((row for row in self.users.values() if row["username"] == username), None)
            if user is None:
                return httpx.Response(401, json={"error": "invalid_grant"})
            if self.passwords.get(user["id"]) != data.get("password"):
                return httpx.Response(401, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "user-token", "expires_in": 60})

        if method == "GET" and path.rstrip("/").endswith("/groups"):
            if self.search_misses > 0:
                self.search_misses -= 1
                return httpx.Response(200, json=[])
            search = request.url.params.get("search")
            rows = list(self.groups.values())
            if search:
                rows = [row for row in rows if search in row["name"]]
            return httpx.Response(200, json=rows)

        if method == "POST" and path.rstrip("/").endswith("/groups"):
            name = body["name"]
            for group in self.groups.values():
                if group["name"] == name:
                    return httpx.Response(
                        409,
                        json={"errorMessage": "A group with this name already exists"},
                    )
            group_id = self._id("group")
            self.groups[group_id] = {
                "id": group_id,
                "name": name,
                "attributes": body.get("attributes") or {},
            }
            self.group_members.setdefault(group_id, set())
            return httpx.Response(
                201,
                headers={"Location": f"http://keycloak.test/admin/realms/atlas/groups/{group_id}"},
            )

        if method == "PUT" and "/groups/" in path and "/users/" not in path:
            group_id = path.rstrip("/").split("/")[-1]
            group = self.groups.get(group_id)
            if group is None:
                return httpx.Response(404, json={"error": "Group not found"})
            group["name"] = body.get("name", group["name"])
            group["attributes"] = body.get("attributes") or {}
            return httpx.Response(204)

        if method == "GET" and path.rstrip("/").endswith("/users"):
            email = (request.url.params.get("email") or "").lower()
            username = (request.url.params.get("username") or "").lower()
            rows = []
            for row in self.users.values():
                if email and row["email"] == email:
                    rows.append(row)
                elif username and row["username"] == username:
                    rows.append(row)
            return httpx.Response(200, json=rows)

        if method == "POST" and path.rstrip("/").endswith("/users"):
            email = str(body.get("email") or "").lower()
            if any(row["email"] == email for row in self.users.values()):
                return httpx.Response(409, json={"errorMessage": "User exists with same email"})
            user_id = self._id("user")
            self.users[user_id] = {
                "id": user_id,
                "username": body.get("username") or email,
                "email": email,
                "enabled": True,
                "emailVerified": body.get("emailVerified", True),
                "firstName": body.get("firstName") or "",
                "lastName": body.get("lastName") or "",
                "attributes": body.get("attributes") or {},
            }
            return httpx.Response(
                201,
                headers={"Location": f"http://keycloak.test/admin/realms/atlas/users/{user_id}"},
            )

        if "/users/" in path:
            rest = path.split("/users/", 1)[1]
            user_id, _, extra = rest.partition("/")
            extra = extra.rstrip("/")
            user = self.users.get(user_id)
            if extra == "reset-password" and method == "PUT":
                if user is None:
                    return httpx.Response(404, json={"error": "User not found"})
                value = str(body.get("value") or "")
                if len(value) < 8:
                    return httpx.Response(
                        400, json={"errorMessage": "Invalid password: minimum length 8."}
                    )
                self.passwords[user_id] = value
                return httpx.Response(204)
            if extra.startswith("groups/") and method == "PUT":
                group_id = extra.split("/", 1)[1]
                self.group_members.setdefault(group_id, set()).add(user_id)
                return httpx.Response(204)
            if extra == "role-mappings/realm" and method == "GET":
                attrs = (user or {}).get("attributes") or {}
                flag = attrs.get("platform_admin") or []
                roles = [{"name": "platform_admin"}] if "true" in flag else []
                return httpx.Response(200, json=roles)
            if extra == "" and method == "GET":
                if user is None:
                    return httpx.Response(404, json={"error": "User not found"})
                return httpx.Response(200, json=user)
            if extra == "" and method == "PUT":
                if user is None:
                    return httpx.Response(404, json={"error": "User not found"})
                if self.profile_put_error:
                    return httpx.Response(
                        400, json={"errorMessage": self.profile_put_error}
                    )
                new_email = str(body.get("email") or "").strip().lower()
                new_username = str(body.get("username") or "").strip().lower()
                current_username = str(user.get("username") or "").strip().lower()
                if new_username and new_username != current_username:
                    return httpx.Response(
                        400,
                        json={
                            "field": "username",
                            "errorMessage": "error-user-attribute-read-only",
                            "params": ["username"],
                        },
                    )
                for other_id, other in self.users.items():
                    if other_id == user_id:
                        continue
                    if new_email and other.get("email") == new_email:
                        return httpx.Response(
                            409, json={"errorMessage": "User exists with same email"}
                        )
                    if new_username and other.get("username") == new_username:
                        return httpx.Response(
                            409, json={"errorMessage": "User exists with same username"}
                        )
                user["username"] = body.get("username", user["username"])
                user["email"] = new_email or user["email"]
                user["enabled"] = body.get("enabled", user.get("enabled", True))
                user["emailVerified"] = body.get(
                    "emailVerified", user.get("emailVerified", True)
                )
                user["firstName"] = body.get("firstName", user.get("firstName") or "")
                user["lastName"] = body.get("lastName", user.get("lastName") or "")
                if "attributes" in body:
                    user["attributes"] = body.get("attributes") or {}
                return httpx.Response(204)
            if extra == "" and method == "DELETE":
                self.users.pop(user_id, None)
                self.passwords.pop(user_id, None)
                return httpx.Response(204)

        return httpx.Response(404, json={"error": f"unhandled {method} {path}"})


def _client(fake: FakeKeycloak) -> IdentityAdminClient:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(fake.handler),
        base_url="http://keycloak.test",
    )
    return IdentityAdminClient(_admin_settings(), http=http)


def test_validate_password_min_length():
    with pytest.raises(ValueError, match="at least 8"):
        validate_password("short")
    assert validate_password(OWNER_PASSWORD, confirm=OWNER_PASSWORD) == OWNER_PASSWORD


@pytest.mark.asyncio
async def test_create_org_owner_group_and_user():
    fake = FakeKeycloak()
    client = _client(fake)
    result = await client.provision_org_owner(
        email="owner@broker.test",
        display_name="Broker Owner",
        organization_id="org_stockbroker",
        password=OWNER_PASSWORD,
    )
    assert result.invite_pending is False
    assert result.user_id in fake.users
    user = fake.users[result.user_id]
    assert user["username"] == "owner@broker.test"
    assert user["emailVerified"] is True
    assert user["attributes"]["org_role"] == ["org:admin"]
    assert user["attributes"]["platform_admin"] == ["false"]
    group = next(row for row in fake.groups.values() if row["name"] == "org_stockbroker")
    assert group["attributes"]["org_id"] == ["org_stockbroker"]
    assert result.user_id in fake.group_members[group["id"]]
    assert fake.passwords[result.user_id] == OWNER_PASSWORD
    assert OWNER_PASSWORD not in str(result)


@pytest.mark.asyncio
async def test_second_user_reuses_existing_org_group():
    fake = FakeKeycloak()
    client = _client(fake)
    first = await client.create_org_user(
        email="one@acme.test",
        display_name="One",
        role=Role.tenant_admin,
        organization_id="org_demo_acme",
        password=OWNER_PASSWORD,
    )
    second = await client.create_org_user(
        email="two@acme.test",
        display_name="Two",
        role=Role.end_user,
        organization_id="org_demo_acme",
        password=MEMBER_PASSWORD,
    )
    assert len(fake.groups) == 1
    group = next(iter(fake.groups.values()))
    assert group["attributes"]["org_id"] == ["org_demo_acme"]
    assert first.user_id in fake.group_members[group["id"]]
    assert second.user_id in fake.group_members[group["id"]]


@pytest.mark.asyncio
async def test_ensure_org_group_is_idempotent_on_create_conflict():
    fake = FakeKeycloak()
    fake.groups["g-existing"] = {
        "id": "g-existing",
        "name": "org_demo_acme",
        "attributes": {"org_id": ["org_demo_acme"]},
    }
    fake.group_members["g-existing"] = set()
    fake.search_misses = 1
    client = _client(fake)
    group_id = await client.ensure_org_group("org_demo_acme")
    assert group_id == "g-existing"
    assert len(fake.groups) == 1


@pytest.mark.asyncio
async def test_ensure_org_group_backfills_org_id_attribute():
    fake = FakeKeycloak()
    fake.groups["g-existing"] = {
        "id": "g-existing",
        "name": "org_demo_acme",
        "attributes": {},
    }
    fake.group_members["g-existing"] = set()
    client = _client(fake)
    group_id = await client.ensure_org_group("org_demo_acme")
    assert group_id == "g-existing"
    assert fake.groups["g-existing"]["attributes"]["org_id"] == ["org_demo_acme"]


@pytest.mark.asyncio
async def test_create_user_rejects_existing_email():
    fake = FakeKeycloak()
    client = _client(fake)
    await client.create_org_user(
        email="dup@acme.test",
        display_name="First",
        role=Role.end_user,
        organization_id="org_demo_acme",
        password=MEMBER_PASSWORD,
    )
    with pytest.raises(IdentityUserExistsError, match="already in use"):
        await client.create_org_user(
            email="dup@acme.test",
            display_name="Second",
            role=Role.end_user,
            organization_id="org_other",
            password=MEMBER_PASSWORD,
        )


@pytest.mark.asyncio
async def test_member_role_maps_to_org_member_claim():
    fake = FakeKeycloak()
    client = _client(fake)
    result = await client.create_org_user(
        email="member@acme.test",
        display_name="Member",
        role=Role.end_user,
        organization_id="org_demo_acme",
        password=MEMBER_PASSWORD,
    )
    assert fake.users[result.user_id]["attributes"]["org_role"] == ["org:member"]


@pytest.mark.asyncio
async def test_set_password_and_change_password():
    fake = FakeKeycloak()
    client = _client(fake)
    created = await client.create_org_user(
        email="ops@acme.test",
        display_name="Ops",
        role=Role.tenant_admin,
        organization_id="org_demo_acme",
        password=MEMBER_PASSWORD,
    )
    updated = "updated-pass-1"
    await client.set_password(created.user_id, updated)
    assert fake.passwords[created.user_id] == updated
    await client.change_password(
        user_id=created.user_id,
        username="ops@acme.test",
        current_password=updated,
        new_password=OWNER_PASSWORD,
    )
    assert fake.passwords[created.user_id] == OWNER_PASSWORD
    with pytest.raises(IdentityPasswordError, match="incorrect"):
        await client.change_password(
            user_id=created.user_id,
            username="ops@acme.test",
            current_password="wrong-password",  # noqa: S106
            new_password=MEMBER_PASSWORD,
        )


@pytest.mark.asyncio
async def test_cannot_reset_platform_admin_password():
    fake = FakeKeycloak()
    client = _client(fake)
    created = await client.create_org_user(
        email="admin@atlas.test",
        display_name="Platform",
        role=Role.tenant_admin,
        organization_id="org_demo_acme",
        password=OWNER_PASSWORD,
    )
    fake.users[created.user_id]["attributes"]["platform_admin"] = ["true"]
    with pytest.raises(IdentityForbiddenError, match="platform administrator"):
        await client.set_password(created.user_id, MEMBER_PASSWORD)


@pytest.mark.asyncio
async def test_update_org_user_profile_email_role_and_password():
    fake = FakeKeycloak()
    client = _client(fake)
    created = await client.create_org_user(
        email="ops@acme.test",
        display_name="Ops",
        role=Role.end_user,
        organization_id="org_demo_acme",
        password=MEMBER_PASSWORD,
    )
    updated_password = "updated-pass-1"  # noqa: S105
    await client.update_org_user(
        created.user_id,
        email="ops.lead@acme.test",
        display_name="Ops Lead",
        role=Role.tenant_admin,
        password=updated_password,
    )
    user = fake.users[created.user_id]
    assert user["username"] == "ops@acme.test"
    assert user["email"] == "ops.lead@acme.test"
    assert user["firstName"] == "Ops"
    assert user["lastName"] == "Lead"
    assert user["attributes"]["org_role"] == ["org:admin"]
    assert fake.passwords[created.user_id] == updated_password
    assert updated_password not in str(user)


@pytest.mark.asyncio
async def test_create_org_user_single_word_display_name_sets_last_name():
    fake = FakeKeycloak()
    client = _client(fake)
    created = await client.create_org_user(
        email="babu@atlas.test",
        display_name="Babu",
        role=Role.end_user,
        organization_id="org_demo_acme",
        password=MEMBER_PASSWORD,
    )
    user = fake.users[created.user_id]
    assert user["firstName"] == "Babu"
    assert user["lastName"] == "Babu"


@pytest.mark.asyncio
async def test_update_org_user_applies_password_when_profile_is_read_only():
    fake = FakeKeycloak()
    client = _client(fake)
    created = await client.create_org_user(
        email="ops@acme.test",
        display_name="Ops",
        role=Role.end_user,
        organization_id="org_demo_acme",
        password=MEMBER_PASSWORD,
    )
    fake.profile_put_error = "error-user-attribute-read-only"
    updated_password = "updated-pass-1"  # noqa: S105
    with pytest.raises(IdentityProvisionError, match="cannot be changed"):
        await client.update_org_user(
            created.user_id,
            email="ops.lead@acme.test",
            display_name="Ops Lead",
            password=updated_password,
        )
    assert fake.passwords[created.user_id] == updated_password
    assert fake.users[created.user_id]["email"] == "ops@acme.test"


def test_humanize_read_only_attribute_error():
    assert "cannot be changed" in humanize_identity_error(
        "error-user-attribute-read-only"
    )
    assert humanize_identity_error("User exists with same email") == (
        "User exists with same email"
    )


@pytest.mark.asyncio
async def test_update_org_user_rejects_taken_email():
    fake = FakeKeycloak()
    client = _client(fake)
    first = await client.create_org_user(
        email="one@acme.test",
        display_name="One",
        role=Role.end_user,
        organization_id="org_demo_acme",
        password=MEMBER_PASSWORD,
    )
    await client.create_org_user(
        email="two@acme.test",
        display_name="Two",
        role=Role.end_user,
        organization_id="org_demo_acme",
        password=MEMBER_PASSWORD,
    )
    with pytest.raises(IdentityUserExistsError, match="already in use"):
        await client.update_org_user(first.user_id, email="two@acme.test")


@pytest.mark.asyncio
async def test_cannot_update_platform_admin_user():
    fake = FakeKeycloak()
    client = _client(fake)
    created = await client.create_org_user(
        email="admin@atlas.test",
        display_name="Platform",
        role=Role.tenant_admin,
        organization_id="org_demo_acme",
        password=OWNER_PASSWORD,
    )
    fake.users[created.user_id]["attributes"]["platform_admin"] = ["true"]
    with pytest.raises(IdentityForbiddenError, match="platform administrator"):
        await client.update_org_user(
            created.user_id,
            display_name="Nope",
            role=Role.end_user,
        )
