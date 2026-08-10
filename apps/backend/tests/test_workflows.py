import pytest

from app.agent_runtime.factory import (
    AgentFactoryService,
    WorkflowFactoryService,
    WorkflowRuntimeRequest,
)
from app.db.repositories import AgentRepository, WorkflowRepository


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
    return config, version


@pytest.mark.asyncio
async def test_workflow_rejects_cross_tenant_step(session, tenant_a, tenant_b):
    own, _ = await _published_agent(session, tenant_a, "own-work")
    other, _ = await _published_agent(session, tenant_b, "other-work")
    session.info["tenant_id"] = tenant_a.tenant_id
    repo = WorkflowRepository(session, tenant_a)
    workflow = await repo.create_config(slug="isolated", name="Isolated")
    with pytest.raises(LookupError, match="not found for tenant"):
        await repo.create_draft(
            config_id=workflow.id,
            mode="sequential",
            steps=[
                {
                    "name": "Own",
                    "target_type": "agent",
                    "target_config_id": own.id,
                },
                {
                    "name": "Other",
                    "target_type": "agent",
                    "target_config_id": other.id,
                },
            ],
        )


@pytest.mark.asyncio
async def test_workflow_publish_pins_published_versions(session, tenant_a):
    agent, published = await _published_agent(session, tenant_a, "processor")
    agents = AgentRepository(session, tenant_a)
    draft = await agents.create_draft(
        config_id=agent.id,
        instructions="Unpublished changes",
        model_id="openai:gpt-4.1-mini",
        temperature=0.2,
    )
    workflows = WorkflowRepository(session, tenant_a)
    config = await workflows.create_config(slug="pipeline", name="Pipeline")
    version = await workflows.create_draft(
        config_id=config.id,
        mode="sequential",
        steps=[
            {
                "name": "Process",
                "target_type": "agent",
                "target_config_id": agent.id,
            }
        ],
    )
    assert (await workflows.steps(version.id))[0].agent_version_id == draft.id
    await workflows.publish(version.id)
    assert (await workflows.steps(version.id))[0].agent_version_id == published.id
    assert (await workflows.get_config(config.id)).published_version_id == version.id


@pytest.mark.asyncio
async def test_workflow_factory_builds_ordered_sequential_steps(
    session, tenant_a, monkeypatch
):
    first, _ = await _published_agent(session, tenant_a, "research")
    second, _ = await _published_agent(session, tenant_a, "write")
    repo = WorkflowRepository(session, tenant_a)
    config = await repo.create_config(slug="content", name="Content")
    version = await repo.create_draft(
        config_id=config.id,
        mode="sequential",
        steps=[
            {"name": "Research", "target_type": "agent", "target_config_id": first.id},
            {"name": "Write", "target_type": "agent", "target_config_id": second.id},
        ],
    )
    await repo.publish(version.id)

    class FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.id = kwargs["id"]

        def initialize_agent(self):
            return None

    class FakeModel:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeWorkflow:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.id = kwargs["id"]

        def initialize_workflow(self):
            return None

    monkeypatch.setattr("app.agent_runtime.factory.Agent", FakeAgent)
    monkeypatch.setattr("app.agent_runtime.factory.OpenAIChat", FakeModel)
    monkeypatch.setattr("agno.workflow.Workflow", FakeWorkflow)
    factory = WorkflowFactoryService(
        AgentFactoryService(session, tenant_a, allowed_hosts=set())
    )
    workflow = await factory.create(
        WorkflowRuntimeRequest(version_id=version.id, session_id="workflow-session")
    )
    steps = workflow.kwargs["steps"]
    assert [step.name for step in steps] == ["Research", "Write"]
    assert workflow._saas_metadata["workflow_version_id"] == str(version.id)


@pytest.mark.asyncio
async def test_workflow_rejects_cel_until_evaluator_available(session, tenant_a):
    agent, _ = await _published_agent(session, tenant_a, "conditional")
    repo = WorkflowRepository(session, tenant_a)
    config = await repo.create_config(slug="conditional", name="Conditional")
    version = await repo.create_draft(
        config_id=config.id,
        mode="sequential",
        steps=[
            {
                "name": "Conditional",
                "target_type": "agent",
                "target_config_id": agent.id,
                "condition_expression": "input.priority == 'high'",
            }
        ],
    )
    steps = await repo.steps(version.id)
    assert steps[0].condition_expression == "input.priority == 'high'"

    with pytest.raises(ValueError, match="Invalid CEL condition expression"):
        await repo.create_draft(
            config_id=config.id,
            mode="sequential",
            steps=[
                {
                    "name": "Broken",
                    "target_type": "agent",
                    "target_config_id": agent.id,
                    "condition_expression": "@@@ not valid cel",
                }
            ],
        )


@pytest.mark.asyncio
async def test_workflow_assignments_are_user_and_tenant_scoped(
    session, tenant_a, tenant_b
):
    agent, _ = await _published_agent(session, tenant_a, "assigned-agent")
    repo = WorkflowRepository(session, tenant_a)
    config = await repo.create_config(slug="assigned-flow", name="Assigned Flow")
    version = await repo.create_draft(
        config_id=config.id,
        mode="sequential",
        steps=[
            {
                "name": "Process",
                "target_type": "agent",
                "target_config_id": agent.id,
            }
        ],
    )
    await repo.publish(version.id)

    assert await repo.list_available_for_user("customer-one") == []
    assert await repo.replace_assignments(
        config.id, ["customer-two", "customer-one", "customer-one"]
    ) == ["customer-one", "customer-two"]
    assert [row.id for row in await repo.list_available_for_user("customer-one")] == [
        config.id
    ]
    assert await repo.is_assigned(config.id, "customer-two")
    assert not await repo.is_assigned(config.id, "unassigned")

    session.info["tenant_id"] = tenant_b.tenant_id
    other_repo = WorkflowRepository(session, tenant_b)
    assert await other_repo.list_available_for_user("customer-one") == []
    assert not await other_repo.is_assigned(config.id, "customer-one")


@pytest.mark.asyncio
async def test_workflow_list_versions_and_restore_published_pointer(session, tenant_a):
    agent, _ = await _published_agent(session, tenant_a, "step-a")
    workflows = WorkflowRepository(session, tenant_a)
    config = await workflows.create_config(slug="wf-history", name="WF History")
    v1 = await workflows.create_draft(
        config_id=config.id,
        mode="sequential",
        steps=[
            {
                "name": "One",
                "target_type": "agent",
                "target_config_id": agent.id,
            }
        ],
    )
    await workflows.publish(v1.id)
    v2 = await workflows.create_draft(
        config_id=config.id,
        mode="parallel",
        steps=[
            {
                "name": "Two",
                "target_type": "agent",
                "target_config_id": agent.id,
            }
        ],
    )
    await workflows.publish(v2.id)

    versions = list(await workflows.list_versions(config.id))
    assert [row.version for row in versions] == [2, 1]
    assert (await workflows.get_config(config.id)).published_version_id == v2.id

    restored = await workflows.restore_version(config.id, v1.id)
    assert restored.id == v1.id
    assert restored.mode == "sequential"
    assert (await workflows.get_config(config.id)).published_version_id == v1.id


@pytest.mark.asyncio
async def test_workflow_restore_as_draft_keeps_live(session, tenant_a):
    agent, _ = await _published_agent(session, tenant_a, "step-b")
    workflows = WorkflowRepository(session, tenant_a)
    config = await workflows.create_config(slug="wf-clone", name="WF Clone")
    v1 = await workflows.create_draft(
        config_id=config.id,
        mode="sequential",
        steps=[
            {
                "name": "Original",
                "target_type": "agent",
                "target_config_id": agent.id,
            }
        ],
    )
    await workflows.publish(v1.id)
    v2 = await workflows.create_draft(
        config_id=config.id,
        mode="parallel",
        steps=[
            {
                "name": "Later",
                "target_type": "agent",
                "target_config_id": agent.id,
            }
        ],
    )
    await workflows.publish(v2.id)

    draft = await workflows.restore_version(config.id, v1.id, as_draft=True)
    assert draft.version == 3
    assert draft.mode == "sequential"
    assert (await workflows.steps(draft.id))[0].name == "Original"
    assert (await workflows.get_config(config.id)).published_version_id == v2.id
