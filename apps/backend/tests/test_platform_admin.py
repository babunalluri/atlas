import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.platform import (
    TenantCreate,
    TenantUpdate,
    create_tenant,
    enter_tenant_workspace,
    update_tenant,
)
from app.auth.dependencies import require_roles
from app.db.models import PlatformAuditEvent, Role
from app.tenancy.context import TenantContext


@pytest.fixture
def platform_admin(tenant_a) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_a.tenant_id,
        user_id="platform-owner",
        role=Role.platform_admin,
        clerk_org_id=tenant_a.clerk_org_id,
    )


@pytest.mark.asyncio
async def test_platform_admin_can_create_and_suspend_tenant(session, platform_admin):
    created = await create_tenant(
        TenantCreate(
            name="New Customer",
            slug="new-customer",
            clerk_org_id="org_new_customer",
        ),
        platform_admin,
        session,
    )
    assert created.is_active is True

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
