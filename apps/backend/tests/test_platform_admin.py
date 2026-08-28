import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.platform import (
    TenantCreate,
    TenantUpdate,
    create_tenant,
    enter_tenant_workspace,
    list_platform_audit,
    list_tenants,
    update_tenant,
)
from app.auth.dependencies import require_roles
from app.auth.identity_admin import IdentityUserExistsError, ProvisionedIdentity
from app.db.email_uniqueness import EMAIL_ALREADY_IN_USE
from app.db.models import Membership, PlatformAuditEvent, Role, Tenant
from app.db.repositories import TeamRepository
from app.domains.setup import apply_tenant_domain
from app.domains.types import STOCK_BROKER_ADMIN_DESK_TEAMS
from app.tenancy.context import TenantContext
from app.tenancy.ids import new_id

OWNER_PASSWORD = "owner-pass-1"  # noqa: S105


@pytest.fixture
def platform_admin(tenant_a) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_a.tenant_id,
        user_id="platform-owner",
        role=Role.platform_admin,
        auth_org_id=tenant_a.auth_org_id,
    )


@pytest.mark.asyncio
async def test_platform_admin_can_create_and_suspend_tenant(session, platform_admin):
    created = await create_tenant(
        TenantCreate(
            name="New Customer",
            slug="new-customer",
            auth_org_id="org_new_customer",
            owner_email="owner@new-customer.test",
            owner_password=OWNER_PASSWORD,
            owner_password_confirm=OWNER_PASSWORD,
        ),
        platform_admin,
        session,
    )
    assert created.is_active is True
    assert created.owner_email == "owner@new-customer.test"
    owner = await session.scalar(
        select(Membership).where(Membership.tenant_id == created.id)
    )
    assert owner is not None
    assert owner.email == "owner@new-customer.test"
    assert owner.role == Role.tenant_admin
    assert owner.user_id == "local:owner@new-customer.test"

    entered = await enter_tenant_workspace(created.id, platform_admin, session)
    assert entered.id == created.id

    updated = await update_tenant(
        created.id,
        TenantUpdate(is_active=False),
        platform_admin,
        session,
    )
    assert updated.is_active is False

    events = (
        await session.scalars(
            select(PlatformAuditEvent)
            .where(PlatformAuditEvent.tenant_id == created.id)
            .order_by(PlatformAuditEvent.created_at)
        )
    ).all()
    assert [event.action for event in events] == [
        "tenant.create",
        "tenant.workspace.enter",
        "tenant.update",
    ]
    assert events[-1].actor_id == "platform-owner"


@pytest.mark.asyncio
async def test_create_tenant_assigns_desk_teams_to_owner_sub(
    session, platform_admin, monkeypatch
):
    from app.auth.identity_admin import ProvisionedIdentity

    class FakeIdentity:
        def __init__(self, settings=None, **kwargs):
            del settings, kwargs

        def configured(self) -> bool:
            return True

        async def provision_org_owner(self, **kwargs):
            return ProvisionedIdentity(
                user_id="kc-owner-sub",
                email=kwargs["email"],
                invite_pending=False,
            )

        async def delete_user(self, user_id: str) -> None:
            del user_id

    monkeypatch.setattr("app.api.platform.IdentityAdminClient", FakeIdentity)
    created = await create_tenant(
        TenantCreate(
            name="Acme Broker",
            slug="acme-broker",
            auth_org_id="org_acme_broker",
            domain="stock_broker",
            owner_email="ceo@broker.test",
            owner_display_name="Broker CEO",
            owner_password=OWNER_PASSWORD,
            owner_password_confirm=OWNER_PASSWORD,
        ),
        platform_admin,
        session,
    )
    membership = await session.scalar(
        select(Membership).where(Membership.tenant_id == created.id)
    )
    assert membership is not None
    assert membership.user_id == "kc-owner-sub"
    assert membership.role == Role.tenant_admin

    session.info["tenant_id"] = created.id
    teams = TeamRepository(
        session,
        TenantContext(
            created.id, "kc-owner-sub", Role.tenant_admin, "org_acme_broker"
        ),
    )
    assigned = await teams.assigned_team_ids("kc-owner-sub")
    assert len(assigned) == len(STOCK_BROKER_ADMIN_DESK_TEAMS)
    assert await teams.assigned_team_ids("platform-owner") == []


@pytest.mark.asyncio
async def test_tenant_admin_fails_platform_role_check(tenant_a):
    dependency = require_roles(Role.platform_admin)
    with pytest.raises(HTTPException) as error:
        await dependency(tenant_a)
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_update_missing_tenant_returns_not_found(session, platform_admin):
    with pytest.raises(HTTPException) as error:
        await update_tenant(
            uuid.uuid4(),
            TenantUpdate(name="Missing"),
            platform_admin,
            session,
        )
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_platform_admin_cannot_suspend_home_tenant(session, platform_admin):
    with pytest.raises(HTTPException) as error:
        await update_tenant(
            platform_admin.tenant_id,
            TenantUpdate(is_active=False),
            platform_admin,
            session,
        )
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_platform_admin_header_switches_tenant_context(tenant_a, tenant_b):
    from starlette.requests import Request

    from app.auth.dependencies import require_tenant
    from app.core.settings import Settings

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    settings = Settings(
        auth_disabled=True,
        database_url="sqlite+aiosqlite:///:memory:",
        credential_encryption_key="dev-only-change-me-please-32b",
    )

    def make_scope() -> dict:
        return {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/admin/agents",
            "raw_path": b"/admin/agents",
            "query_string": b"",
            "headers": [
                (b"x-dev-tenant-id", str(tenant_a.tenant_id).encode()),
                (b"x-dev-user-id", b"platform-owner"),
                (b"x-dev-role", b"platform_admin"),
            ],
            "client": ("127.0.0.1", 123),
            "server": ("test", 80),
        }

    home = await require_tenant(
        Request(make_scope(), receive),
        authorization=None,
        x_platform_tenant_id=None,
        settings=settings,
    )
    assert home.tenant_id == tenant_a.tenant_id

    switched = await require_tenant(
        Request(make_scope(), receive),
        authorization=None,
        x_platform_tenant_id=str(tenant_b.tenant_id),
        settings=settings,
    )
    assert switched.tenant_id == tenant_b.tenant_id
    assert switched.role == Role.platform_admin


UPDATED_PASSWORD = "owner-pass-2"  # noqa: S105


def _install_fake_identity(monkeypatch, *, existing_emails: set[str] | None = None):
    recorder: dict[str, list] = {"provision": [], "passwords": [], "deleted": []}
    existing = set(existing_emails or [])

    class FakeIdentity:
        def __init__(self, settings=None, **kwargs):
            del settings, kwargs

        def configured(self) -> bool:
            return True

        async def provision_org_owner(self, **kwargs):
            email = kwargs["email"]
            if email in existing:
                raise IdentityUserExistsError(EMAIL_ALREADY_IN_USE)
            existing.add(email)
            recorder["provision"].append(kwargs)
            return ProvisionedIdentity(
                user_id=f"kc-{email}",
                email=email,
                invite_pending=False,
            )

        async def delete_user(self, user_id: str) -> None:
            recorder["deleted"].append(user_id)

        async def get_user(self, user_id: str):
            return {"id": user_id}

        async def find_user_by_email(self, email: str):
            if email in existing:
                return {"id": f"kc-{email}", "email": email}
            return None

        async def set_password(self, user_id: str, password: str) -> None:
            recorder["passwords"].append({"user_id": user_id, "password": password})

    monkeypatch.setattr("app.api.platform.IdentityAdminClient", FakeIdentity)
    return recorder


@pytest.mark.asyncio
async def test_list_tenants_includes_owner_email_or_none(session, platform_admin):
    created = await create_tenant(
        TenantCreate(
            name="Owned Co",
            slug="owned-co",
            auth_org_id="org_owned_co",
            owner_email="ceo@owned.test",
            owner_password=OWNER_PASSWORD,
            owner_password_confirm=OWNER_PASSWORD,
        ),
        platform_admin,
        session,
    )
    listed = await list_tenants(platform_admin, session)
    by_slug = {row.slug: row for row in listed}
    assert by_slug["acme"].owner_email is None
    assert by_slug["owned-co"].owner_email == "ceo@owned.test"
    assert created.owner_email == "ceo@owned.test"
    dumped = created.model_dump()
    assert OWNER_PASSWORD not in str(dumped)
    assert "owner_password" not in dumped


@pytest.mark.asyncio
async def test_update_tenant_without_owner_creates_keycloak_owner(
    session, platform_admin, monkeypatch
):
    tenant = Tenant(
        id=new_id(),
        name="StockBroker",
        slug="stockbroker-legacy",
        auth_org_id="org_stockbroker_legacy",
        domain="stock_broker",
        branding={},
        timezone="Asia/Kolkata",
        is_active=True,
    )
    session.add(tenant)
    await session.flush()
    await apply_tenant_domain(
        session,
        tenant=tenant,
        actor_user_id="legacy-platform",
        domain="stock_broker",
    )
    recorder = _install_fake_identity(monkeypatch)
    updated = await update_tenant(
        tenant.id,
        TenantUpdate(
            owner_email="owner@stockbroker.test",
            owner_password=OWNER_PASSWORD,
            owner_password_confirm=OWNER_PASSWORD,
        ),
        platform_admin,
        session,
    )
    assert updated.owner_email == "owner@stockbroker.test"
    assert OWNER_PASSWORD not in str(updated.model_dump())
    assert recorder["provision"][0]["email"] == "owner@stockbroker.test"
    assert recorder["provision"][0]["organization_id"] == "org_stockbroker_legacy"
    assert recorder["provision"][0]["password"] == OWNER_PASSWORD

    membership = await session.scalar(
        select(Membership).where(
            Membership.tenant_id == tenant.id,
            Membership.role == Role.tenant_admin,
        )
    )
    assert membership is not None
    assert membership.user_id == "kc-owner@stockbroker.test"
    assert membership.email == "owner@stockbroker.test"

    session.info["tenant_id"] = tenant.id
    teams = TeamRepository(
        session,
        TenantContext(
            tenant.id,
            membership.user_id,
            Role.tenant_admin,
            "org_stockbroker_legacy",
        ),
    )
    assert len(await teams.assigned_team_ids(membership.user_id)) == len(
        STOCK_BROKER_ADMIN_DESK_TEAMS
    )

    events = (
        await session.scalars(
            select(PlatformAuditEvent)
            .where(PlatformAuditEvent.tenant_id == tenant.id)
            .order_by(PlatformAuditEvent.created_at)
        )
    ).all()
    assert any(event.action == "tenant.update" for event in events)
    owner_audit = next(
        event for event in events if event.action == "tenant.update"
    )
    assert owner_audit.details["owner_created"]["email"] == "owner@stockbroker.test"
    assert OWNER_PASSWORD not in str(owner_audit.details)


@pytest.mark.asyncio
async def test_update_tenant_with_owner_sets_keycloak_password(
    session, platform_admin, monkeypatch
):
    recorder = _install_fake_identity(monkeypatch)
    created = await create_tenant(
        TenantCreate(
            name="Acme Broker",
            slug="acme-broker-owned",
            auth_org_id="org_acme_broker_owned",
            domain="stock_broker",
            owner_email="ceo@broker.test",
            owner_password=OWNER_PASSWORD,
            owner_password_confirm=OWNER_PASSWORD,
        ),
        platform_admin,
        session,
    )
    updated = await update_tenant(
        created.id,
        TenantUpdate(
            name="Acme Broker Ltd",
            owner_password=UPDATED_PASSWORD,
            owner_password_confirm=UPDATED_PASSWORD,
        ),
        platform_admin,
        session,
    )
    assert updated.name == "Acme Broker Ltd"
    assert updated.owner_email == "ceo@broker.test"
    assert UPDATED_PASSWORD not in str(updated.model_dump())
    assert recorder["passwords"] == [
        {"user_id": "kc-ceo@broker.test", "password": UPDATED_PASSWORD}
    ]


@pytest.mark.asyncio
async def test_update_tenant_new_owner_email_conflict_returns_409(
    session, platform_admin, monkeypatch
):
    _install_fake_identity(monkeypatch, existing_emails={"taken@other.test"})
    created = await create_tenant(
        TenantCreate(
            name="Conflict Co",
            slug="conflict-co",
            auth_org_id="org_conflict_co",
            owner_email="ceo@conflict.test",
            owner_password=OWNER_PASSWORD,
            owner_password_confirm=OWNER_PASSWORD,
        ),
        platform_admin,
        session,
    )
    with pytest.raises(HTTPException) as error:
        await update_tenant(
            created.id,
            TenantUpdate(
                owner_email="taken@other.test",
                owner_password=OWNER_PASSWORD,
                owner_password_confirm=OWNER_PASSWORD,
            ),
            platform_admin,
            session,
        )
    assert error.value.status_code == 409
    assert error.value.detail == EMAIL_ALREADY_IN_USE


@pytest.mark.asyncio
async def test_create_tenant_rejects_duplicate_owner_email(session, platform_admin):
    await create_tenant(
        TenantCreate(
            name="First Co",
            slug="first-co",
            auth_org_id="org_first_co",
            owner_email="ceo@shared.test",
            owner_password=OWNER_PASSWORD,
            owner_password_confirm=OWNER_PASSWORD,
        ),
        platform_admin,
        session,
    )
    with pytest.raises(HTTPException) as error:
        await create_tenant(
            TenantCreate(
                name="Second Co",
                slug="second-co",
                auth_org_id="org_second_co",
                owner_email="ceo@shared.test",
                owner_password=OWNER_PASSWORD,
                owner_password_confirm=OWNER_PASSWORD,
            ),
            platform_admin,
            session,
        )
    assert error.value.status_code == 409
    assert error.value.detail == EMAIL_ALREADY_IN_USE


@pytest.mark.asyncio
async def test_update_tenant_requires_password_when_creating_owner(
    session, platform_admin
):
    tenant = Tenant(
        id=new_id(),
        name="Babu",
        slug="babu-legacy",
        auth_org_id="org_babu_legacy",
        branding={},
        is_active=True,
    )
    session.add(tenant)
    await session.flush()
    with pytest.raises(HTTPException) as error:
        await update_tenant(
            tenant.id,
            TenantUpdate(owner_email="babu@atlas.test"),
            platform_admin,
            session,
        )
    assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_list_platform_audit_includes_payload_and_actor_profile(
    session, platform_admin, tenant_a
):
    session.add(
        Membership(
            id=new_id(),
            tenant_id=tenant_a.tenant_id,
            user_id=platform_admin.user_id,
            display_name="Platform Owner",
            email="ops@atlas.test",
            role=Role.platform_admin,
            is_active=True,
        )
    )
    await session.flush()
    created = await create_tenant(
        TenantCreate(
            name="Audit Co",
            slug="audit-co",
            auth_org_id="org_audit_co",
            owner_email="owner@audit-co.test",
            owner_password=OWNER_PASSWORD,
            owner_password_confirm=OWNER_PASSWORD,
        ),
        platform_admin,
        session,
    )
    await enter_tenant_workspace(created.id, platform_admin, session)
    await update_tenant(
        created.id,
        TenantUpdate(name="Audit Company"),
        platform_admin,
        session,
    )

    events = await list_platform_audit(platform_admin, session)
    by_action = {event.action: event for event in events}
    assert "tenant.create" in by_action
    assert "tenant.workspace.enter" in by_action
    assert "tenant.update" in by_action

    enter = by_action["tenant.workspace.enter"]
    assert enter.details == {"slug": "audit-co"}
    assert enter.actor_id == "platform-owner"
    assert enter.actor_email == "ops@atlas.test"
    assert enter.actor_name == "Platform Owner"
    assert enter.tenant_id == created.id

    update = by_action["tenant.update"]
    assert update.details["name"] == {"from": "Audit Co", "to": "Audit Company"}
    assert OWNER_PASSWORD not in str(update.details)

    create = by_action["tenant.create"]
    assert create.details["slug"] == "audit-co"
    assert create.details["owner_email"] == "owner@audit-co.test"


@pytest.mark.asyncio
async def test_non_platform_admin_cannot_edit_tenant(tenant_a):
    dependency = require_roles(Role.platform_admin)
    with pytest.raises(HTTPException) as error:
        await dependency(tenant_a)
    assert error.value.status_code == 403
