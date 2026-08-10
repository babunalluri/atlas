"""Automatic org invite + membership claim for Atlas users."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.auth.identity_admin import (
    IdentityAdminClient,
    IdentityProvisionError,
    pending_user_id,
)
from app.db.models import Role
from app.db.repositories import MembershipRepository, WorkflowRepository


@pytest.mark.asyncio
async def test_provision_invites_new_email(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_live_key")
    from app.core.settings import get_settings

    get_settings.cache_clear()
    client = IdentityAdminClient(get_settings())

    with patch.object(
        client, "find_user_by_email", new=AsyncMock(return_value=None)
    ), patch.object(
        client,
        "create_user",
        new=AsyncMock(return_value={"id": "user_created_1"}),
    ), patch.object(
        client, "ensure_organization_membership", new=AsyncMock()
    ) as membership, patch.object(
        client, "create_app_invitation", new=AsyncMock(return_value={"id": "inv_1"})
    ):
        result = await client.provision_tenant_user(
            email="New.User@Example.com",
            display_name="New User",
            role=Role.end_user,
            organization_id="org_demo_acme",
            inviter_user_id="user_admin",
            redirect_url="http://localhost:3000/sign-in",
        )
    assert result.invite_pending is False
    assert result.user_id == "user_created_1"
    membership.assert_awaited_once()


@pytest.mark.asyncio
async def test_provision_falls_back_to_org_invite(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_live_key")
    from app.core.settings import get_settings

    get_settings.cache_clear()
    client = IdentityAdminClient(get_settings())

    with patch.object(
        client, "find_user_by_email", new=AsyncMock(return_value=None)
    ), patch.object(
        client,
        "create_user",
        new=AsyncMock(side_effect=IdentityProvisionError("create blocked")),
    ), patch.object(
        client,
        "create_organization_invitation",
        new=AsyncMock(return_value={"id": "orginv_1"}),
    ) as invite:
        result = await client.provision_tenant_user(
            email="fallback@example.com",
            display_name="Fallback",
            role=Role.end_user,
            organization_id="org_demo_acme",
            inviter_user_id="user_admin",
            redirect_url="http://localhost:3000/sign-in",
        )
    assert result.invite_pending is True
    assert result.user_id == pending_user_id("fallback@example.com")
    invite.assert_awaited_once()


@pytest.mark.asyncio
async def test_provision_links_existing_account(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_live_key")
    from app.core.settings import get_settings

    get_settings.cache_clear()
    client = IdentityAdminClient(get_settings())

    with patch.object(
        client,
        "find_user_by_email",
        new=AsyncMock(return_value={"id": "user_existing"}),
    ), patch.object(
        client, "ensure_organization_membership", new=AsyncMock()
    ) as membership, patch.object(
        type(client.settings), "is_development", property(lambda self: False)
    ):
        result = await client.provision_tenant_user(
            email="existing@example.com",
            display_name="Existing",
            role=Role.tenant_admin,
            organization_id="org_demo_acme",
            inviter_user_id="user_admin",
            redirect_url="http://localhost:3000/sign-in",
        )
    assert result.invite_pending is False
    assert result.user_id == "user_existing"
    membership.assert_awaited_once()
    assert membership.await_args.kwargs["role"] == "org:admin"


@pytest.mark.asyncio
async def test_claim_pending_membership_and_workflows(session, tenant_a):
    users = MembershipRepository(session, tenant_a)
    workflows = WorkflowRepository(session, tenant_a)
    from app.db.repositories import AgentRepository

    session.info["tenant_id"] = tenant_a.tenant_id
    agents = AgentRepository(session, tenant_a)
    agent = await agents.create_config(slug="claim-agent", name="Claim Agent")
    version = await agents.create_draft(
        config_id=agent.id,
        instructions="x",
        model_id="openai:gpt-4.1-mini",
        temperature=0.2,
    )
    await agents.publish(version.id)
    flow = await workflows.create_config(slug="claim-flow", name="Claim Flow")
    draft = await workflows.create_draft(
        config_id=flow.id,
        mode="sequential",
        steps=[
            {
                "name": "Step",
                "target_type": "agent",
                "target_config_id": agent.id,
            }
        ],
    )
    await workflows.publish(draft.id)

    pending = await users.create(
        user_id=pending_user_id("claim@example.com"),
        display_name="Claim Me",
        email="claim@example.com",
        role=Role.end_user,
    )
    await workflows.replace_user_assignments(pending.user_id, [flow.id])

    claimed = await users.claim_pending_by_email(
        email="claim@example.com", user_id="user_real_123"
    )
    assert claimed is not None
    assert claimed.user_id == "user_real_123"
    assert await workflows.assigned_workflow_ids("user_real_123") == [flow.id]
    assert await workflows.assigned_workflow_ids(pending_user_id("claim@example.com")) == []


@pytest.mark.asyncio
async def test_provision_requires_secret(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_replace_me")
    from app.core.settings import get_settings

    get_settings.cache_clear()
    client = IdentityAdminClient(get_settings())
    with pytest.raises(IdentityProvisionError, match="not configured"):
        await client.provision_tenant_user(
            email="a@b.com",
            display_name="A",
            role=Role.end_user,
            organization_id="org_x",
            inviter_user_id="user_x",
            redirect_url="http://localhost:3000/sign-in",
        )
