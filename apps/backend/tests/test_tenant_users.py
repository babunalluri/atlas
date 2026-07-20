import pytest

from app.db.models import Role
from app.db.repositories import AgentRepository, MembershipRepository, WorkflowRepository


async def _published_agent(session, tenant, slug):
    session.info["tenant_id"] = tenant.tenant_id
    repo = AgentRepository(session, tenant)
    config = await repo.create_config(slug=slug, name=slug.title())
    version = await repo.create_draft(
        config_id=config.id,
        instructions=f"Handle {slug}",
        model_id="openai:gpt-4.1-mini",
        temperature=0.2,
    )
    await repo.publish(version.id)
    return config


async def _published_workflow(session, tenant, agent_id, slug="user-flow"):
    workflows = WorkflowRepository(session, tenant)
    config = await workflows.create_config(slug=slug, name=slug.title())
    version = await workflows.create_draft(
        config_id=config.id,
        mode="sequential",
        steps=[
            {
                "name": "Step",
                "target_type": "agent",
                "target_config_id": agent_id,
            }
        ],
    )
    await workflows.publish(version.id)
    return config


@pytest.mark.asyncio
async def test_tenant_user_crud_and_workflow_assignment(session, tenant_a):
    agent = await _published_agent(session, tenant_a, "user-agent")
    workflow = await _published_workflow(session, tenant_a, agent.id)
    users = MembershipRepository(session, tenant_a)
    workflows = WorkflowRepository(session, tenant_a)

    membership = await users.create(
        user_id="user_abc",
        display_name="Ada Admin",
        email="ada@example.com",
        role=Role.end_user,
    )
    assert membership.display_name == "Ada Admin"
    assert await users.get_by_user_id("user_abc") is not None

    assigned = await workflows.replace_user_assignments(
        membership.user_id, [workflow.id]
    )
    assert assigned == [workflow.id]
    assert [row.slug for row in await workflows.list_available_for_user("user_abc")] == [
        "user-flow"
    ]

    updated = await users.update(
        membership.id,
        role=Role.tenant_admin,
        is_active=False,
    )
    assert updated is not None
    assert updated.role == Role.tenant_admin
    assert updated.is_active is False

    deleted = await users.delete(membership.id)
    assert deleted is not None
    assert await users.get_by_user_id("user_abc") is None
    assert await workflows.list_available_for_user("user_abc") == []


@pytest.mark.asyncio
async def test_user_assignment_requires_published_workflow(session, tenant_a):
    agent = await _published_agent(session, tenant_a, "draft-agent")
    users = MembershipRepository(session, tenant_a)
    workflows = WorkflowRepository(session, tenant_a)
    draft = await workflows.create_config(slug="draft-only", name="Draft Only")
    await workflows.create_draft(
        config_id=draft.id,
        mode="sequential",
        steps=[
            {
                "name": "Step",
                "target_type": "agent",
                "target_config_id": agent.id,
            }
        ],
    )
    membership = await users.create(
        user_id="user_draft",
        display_name="Draft User",
        email=None,
        role=Role.end_user,
    )
    with pytest.raises(ValueError, match="must be published"):
        await workflows.replace_user_assignments(membership.user_id, [draft.id])
