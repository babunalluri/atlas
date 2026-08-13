"""Tenant user create + password update via Keycloak (mocked)."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.identity_admin import (
    IdentityForbiddenError,
    IdentityNotFoundError,
    IdentityUserExistsError,
    ProvisionedIdentity,
)
from app.db.models import Base, Membership, Role, Tenant
from app.main import app
from app.tenancy.context import TenantContext

TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
MEMBER_PASSWORD = "member-pass-1"  # noqa: S105
UPDATED_PASSWORD = "updated-pass-1"  # noqa: S105


@pytest.fixture
async def users_db(monkeypatch):
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    tenant = Tenant(
        id=TENANT_ID,
        auth_org_id="org_demo_acme",
        slug="acme",
        name="Acme Corp",
        branding={},
    )
    async with factory() as session:
        session.add(tenant)
        await session.commit()

    def make_session():
        return factory()

    for target in (
        "app.db.session.SessionFactory",
        "app.auth.dependencies.SessionFactory",
        "app.agent_runtime.agent_os.SessionFactory",
    ):
        monkeypatch.setattr(target, make_session)

    yield {
        "factory": factory,
        "tenant": TenantContext(
            tenant_id=tenant.id,
            user_id="tenant-admin-1",
            role=Role.tenant_admin,
            auth_org_id=tenant.auth_org_id,
        ),
    }
    await eng.dispose()


class FakeIdentity:
    def __init__(self, settings=None, **kwargs):
        del settings, kwargs
        self.created: list[dict] = []
        self.updated: list[dict] = []
        self.users: dict[str, dict] = {}
        self.passwords: dict[str, str] = {}
        self.platform_admins: set[str] = set()
        self.existing_emails: set[str] = set()

    def configured(self) -> bool:
        return True

    async def provision_tenant_user(self, **kwargs):
        email = kwargs["email"]
        if email in self.existing_emails:
            raise IdentityUserExistsError("user already exists")
        self.existing_emails.add(email)
        user_id = f"kc-{email}"
        role = kwargs.get("role")
        org_role = "org:admin" if role == Role.tenant_admin else "org:member"
        self.users[user_id] = {
            "id": user_id,
            "email": email,
            "username": email,
            "firstName": kwargs.get("display_name") or email,
            "lastName": "",
            "attributes": {
                "platform_admin": ["false"],
                "org_role": [org_role],
            },
        }
        self.created.append({"user_id": user_id, **kwargs})
        if kwargs.get("password"):
            self.passwords[user_id] = kwargs["password"]
        return ProvisionedIdentity(
            user_id=user_id,
            email=email,
            invite_pending=False,
        )

    async def delete_user(self, user_id: str) -> None:
        del user_id

    async def get_user(self, user_id: str) -> dict | None:
        if user_id in self.platform_admins:
            row = self.users.get(user_id) or {"id": user_id, "attributes": {}}
            attrs = dict(row.get("attributes") or {})
            attrs["platform_admin"] = ["true"]
            return {**row, "id": user_id, "attributes": attrs}
        if user_id in self.users:
            return self.users[user_id]
        if user_id in self.passwords:
            return {"id": user_id, "attributes": {"platform_admin": ["false"]}}
        return None

    async def find_user_by_email(self, email: str) -> dict | None:
        normalized = email.strip().lower()
        for row in self.users.values():
            if str(row.get("email") or "").strip().lower() == normalized:
                return row
            if str(row.get("username") or "").strip().lower() == normalized:
                return row
        return None

    async def is_platform_admin_user(self, user: dict) -> bool:
        user_id = str(user.get("id") or "")
        if user_id in self.platform_admins:
            return True
        attrs = user.get("attributes") if isinstance(user.get("attributes"), dict) else {}
        flag = attrs.get("platform_admin") or []
        return "true" in flag

    async def set_password(self, user_id: str, password: str) -> None:
        if user_id in self.platform_admins:
            raise IdentityForbiddenError("Cannot edit or reset a platform administrator")
        self.passwords[user_id] = password

    async def update_org_user(
        self,
        user_id: str,
        *,
        email: str | None = None,
        display_name: str | None = None,
        role: Role | None = None,
        password: str | None = None,
    ) -> dict:
        user = await self.get_user(user_id)
        if user is None:
            raise IdentityNotFoundError("Identity user not found")
        if user_id in self.platform_admins or await self.is_platform_admin_user(user):
            raise IdentityForbiddenError("Cannot edit or reset a platform administrator")
        if email:
            existing = await self.find_user_by_email(email)
            if existing is not None and str(existing.get("id") or "") != user_id:
                raise IdentityUserExistsError("user already exists")
            previous = str(user.get("email") or "")
            if previous and previous != email:
                self.existing_emails.discard(previous)
            self.existing_emails.add(email)
            user["email"] = email
            user["username"] = email
        if display_name is not None:
            user["firstName"] = display_name
        if role is not None:
            attrs = dict(user.get("attributes") or {})
            attrs["org_role"] = [
                "org:admin" if role == Role.tenant_admin else "org:member"
            ]
            user["attributes"] = attrs
        if password:
            await self.set_password(user_id, password)
        self.users[user_id] = user
        self.updated.append(
            {
                "user_id": user_id,
                "email": email,
                "display_name": display_name,
                "role": role,
                "password": password,
            }
        )
        return user

    async def change_password(self, **kwargs) -> None:
        self.passwords[kwargs["user_id"]] = kwargs["new_password"]


@pytest.fixture
def fake_identity(monkeypatch):
    client = FakeIdentity()
    monkeypatch.setattr("app.api.users.IdentityAdminClient", lambda settings=None: client)
    return client


def _headers(role: Role = Role.tenant_admin, user_id: str = "tenant-admin-1") -> dict[str, str]:
    return {
        "x-dev-tenant-id": str(TENANT_ID),
        "x-dev-user-id": user_id,
        "x-dev-role": role.value,
    }


@pytest.mark.asyncio
async def test_create_user_with_password_uses_keycloak(users_db, fake_identity):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/admin/users",
            headers=_headers(),
            json={
                "email": "trader@acme.test",
                "display_name": "Trader",
                "role": "end_user",
                "password": MEMBER_PASSWORD,
                "password_confirm": MEMBER_PASSWORD,
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["user_id"] == "kc-trader@acme.test"
        assert body["invite_pending"] is False
        assert body.get("temporary_password") in {None, ""}
        assert MEMBER_PASSWORD not in created.text
        assert fake_identity.created[0]["password"] == MEMBER_PASSWORD
        assert fake_identity.created[0]["role"] == Role.end_user

        duplicate = await client.post(
            "/admin/users",
            headers=_headers(),
            json={
                "email": "trader@acme.test",
                "display_name": "Trader 2",
                "role": "end_user",
                "password": MEMBER_PASSWORD,
                "password_confirm": MEMBER_PASSWORD,
            },
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"] == "This email is already in use."
        assert len(fake_identity.created) == 1


@pytest.mark.asyncio
async def test_set_password_and_block_platform_admin(users_db, fake_identity):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/admin/users",
            headers=_headers(),
            json={
                "email": "ops@acme.test",
                "display_name": "Ops",
                "role": "tenant_admin",
                "password": MEMBER_PASSWORD,
                "password_confirm": MEMBER_PASSWORD,
            },
        )
        assert created.status_code == 201, created.text
        membership_id = created.json()["id"]
        updated = await client.post(
            f"/admin/users/{membership_id}/password",
            headers=_headers(),
            json={"password": UPDATED_PASSWORD, "password_confirm": UPDATED_PASSWORD},
        )
        assert updated.status_code == 204
        assert fake_identity.passwords["kc-ops@acme.test"] == UPDATED_PASSWORD
        assert UPDATED_PASSWORD not in (updated.text or "")

        fake_identity.platform_admins.add("kc-ops@acme.test")
        blocked = await client.post(
            f"/admin/users/{membership_id}/password",
            headers=_headers(),
            json={"password": MEMBER_PASSWORD, "password_confirm": MEMBER_PASSWORD},
        )
        assert blocked.status_code == 403


@pytest.mark.asyncio
async def test_change_own_password(users_db, fake_identity):
    fake_identity.passwords["tenant-admin-1"] = MEMBER_PASSWORD
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        changed = await client.post(
            "/admin/users/me/password",
            headers=_headers(),
            json={
                "current_password": MEMBER_PASSWORD,
                "new_password": UPDATED_PASSWORD,
                "new_password_confirm": UPDATED_PASSWORD,
            },
        )
        assert changed.status_code == 204
        assert fake_identity.passwords["tenant-admin-1"] == UPDATED_PASSWORD


@pytest.mark.asyncio
async def test_tenant_admin_can_patch_user(users_db, fake_identity):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/admin/users",
            headers=_headers(),
            json={
                "email": "trader@acme.test",
                "display_name": "Trader",
                "role": "end_user",
                "password": MEMBER_PASSWORD,
                "password_confirm": MEMBER_PASSWORD,
            },
        )
        assert created.status_code == 201, created.text
        membership_id = created.json()["id"]
        patched = await client.patch(
            f"/admin/users/{membership_id}",
            headers=_headers(),
            json={
                "display_name": "Lead Trader",
                "email": "lead@acme.test",
                "role": "tenant_admin",
            },
        )
        assert patched.status_code == 200, patched.text
        body = patched.json()
        assert body["display_name"] == "Lead Trader"
        assert body["email"] == "lead@acme.test"
        assert body["role"] == "tenant_admin"
        assert MEMBER_PASSWORD not in patched.text
        assert fake_identity.updated[-1]["display_name"] == "Lead Trader"
        assert fake_identity.updated[-1]["email"] == "lead@acme.test"
        assert fake_identity.updated[-1]["role"] == Role.tenant_admin
        user = fake_identity.users["kc-trader@acme.test"]
        assert user["attributes"]["org_role"] == ["org:admin"]
        assert user["email"] == "lead@acme.test"


@pytest.mark.asyncio
async def test_patch_user_password_and_block_platform_admin(users_db, fake_identity):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/admin/users",
            headers=_headers(),
            json={
                "email": "ops@acme.test",
                "display_name": "Ops",
                "role": "tenant_admin",
                "password": MEMBER_PASSWORD,
                "password_confirm": MEMBER_PASSWORD,
            },
        )
        assert created.status_code == 201, created.text
        membership_id = created.json()["id"]
        patched = await client.patch(
            f"/admin/users/{membership_id}",
            headers=_headers(),
            json={
                "password": UPDATED_PASSWORD,
                "password_confirm": UPDATED_PASSWORD,
            },
        )
        assert patched.status_code == 200, patched.text
        assert fake_identity.passwords["kc-ops@acme.test"] == UPDATED_PASSWORD
        assert UPDATED_PASSWORD not in patched.text
        assert patched.json().get("temporary_password") in {None, ""}

        fake_identity.platform_admins.add("kc-ops@acme.test")
        blocked = await client.patch(
            f"/admin/users/{membership_id}",
            headers=_headers(),
            json={"display_name": "Nope"},
        )
        assert blocked.status_code == 403


@pytest.mark.asyncio
async def test_end_user_cannot_patch_user(users_db, fake_identity):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/admin/users",
            headers=_headers(),
            json={
                "email": "trader@acme.test",
                "display_name": "Trader",
                "role": "end_user",
                "password": MEMBER_PASSWORD,
                "password_confirm": MEMBER_PASSWORD,
            },
        )
        assert created.status_code == 201, created.text
        membership_id = created.json()["id"]
        denied = await client.patch(
            f"/admin/users/{membership_id}",
            headers=_headers(Role.end_user, "end-user-1"),
            json={"display_name": "Hacker"},
        )
        assert denied.status_code == 403


@pytest.mark.asyncio
async def test_tenant_admin_cannot_delete_admin_but_can_delete_member(
    users_db, fake_identity
):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin = await client.post(
            "/admin/users",
            headers=_headers(),
            json={
                "email": "owner@acme.test",
                "display_name": "Owner",
                "role": "tenant_admin",
                "password": MEMBER_PASSWORD,
                "password_confirm": MEMBER_PASSWORD,
            },
        )
        assert admin.status_code == 201, admin.text
        admin_id = admin.json()["id"]
        denied = await client.delete(
            f"/admin/users/{admin_id}",
            headers=_headers(),
        )
        assert denied.status_code == 403
        assert "cannot be deleted" in denied.json()["detail"].lower()

        member = await client.post(
            "/admin/users",
            headers=_headers(),
            json={
                "email": "member@acme.test",
                "display_name": "Member",
                "role": "end_user",
                "password": MEMBER_PASSWORD,
                "password_confirm": MEMBER_PASSWORD,
            },
        )
        assert member.status_code == 201, member.text
        member_id = member.json()["id"]
        deleted = await client.delete(
            f"/admin/users/{member_id}",
            headers=_headers(),
        )
        assert deleted.status_code == 204

        listed = await client.get("/admin/users", headers=_headers())
        assert listed.status_code == 200
        ids = {row["id"] for row in listed.json()}
        assert admin_id in ids
        assert member_id not in ids


@pytest.mark.asyncio
async def test_create_user_rejects_email_used_in_other_tenant(users_db, fake_identity):
    other_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    async with users_db["factory"]() as session:
        session.add(
            Tenant(
                id=other_id,
                auth_org_id="org_demo_globex",
                slug="globex",
                name="Globex Inc",
                branding={},
            )
        )
        session.add(
            Membership(
                id=uuid.uuid4(),
                tenant_id=other_id,
                user_id="local:shared@acme.test",
                display_name="Shared",
                email="shared@acme.test",
                role=Role.end_user,
                is_active=True,
            )
        )
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        duplicate = await client.post(
            "/admin/users",
            headers=_headers(),
            json={
                "email": "shared@acme.test",
                "display_name": "Babu",
                "role": "end_user",
                "password": MEMBER_PASSWORD,
                "password_confirm": MEMBER_PASSWORD,
            },
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"] == "This email is already in use."
        assert fake_identity.created == []


@pytest.mark.asyncio
async def test_patch_user_rejects_email_used_by_another_user(users_db, fake_identity):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/admin/users",
            headers=_headers(),
            json={
                "email": "one@acme.test",
                "display_name": "One",
                "role": "end_user",
                "password": MEMBER_PASSWORD,
                "password_confirm": MEMBER_PASSWORD,
            },
        )
        assert first.status_code == 201, first.text
        second = await client.post(
            "/admin/users",
            headers=_headers(),
            json={
                "email": "two@acme.test",
                "display_name": "Two",
                "role": "end_user",
                "password": MEMBER_PASSWORD,
                "password_confirm": MEMBER_PASSWORD,
            },
        )
        assert second.status_code == 201, second.text
        conflict = await client.patch(
            f"/admin/users/{second.json()['id']}",
            headers=_headers(),
            json={"email": "one@acme.test"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == "This email is already in use."
