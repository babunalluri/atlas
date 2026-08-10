"""Automatic org invite + membership claim for Atlas users."""

from __future__ import annotations

import pytest

from app.auth.identity_admin import (
    IdentityAdminClient,
    pending_user_id,
)
from app.core.settings import get_settings
from app.db.models import Role
from app.db.repositories import MembershipRepository, WorkflowRepository


@pytest.mark.asyncio
async def test_provision_creates_pending_membership():
    client = IdentityAdminClient(get_settings())
    assert client.configured() is True
    result = await client.provision_tenant_user(
        email="Pending.User@Example.com",
        display_name="Pending User",
        role=Role.end_user,
        organization_id="org_demo_acme",
        inviter_user_id="user_admin",
        redirect_url="http://localhost:3000/sign-in",
    )
    assert result.invite_pending is True
    assert result.user_id == pending_user_id("pending.user@example.com")
    assert "Keycloak" in result.detail


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
