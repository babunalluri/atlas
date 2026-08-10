import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.api.platform import (
    TenantImportIn,
    get_tenant_catalog,
    import_tenant_resources,
)
from app.auth.dependencies import require_roles
from app.db.models import (
    PlatformAuditEvent,
    Role,
    Tenant,
)
from app.db.repositories import (
    AgentRepository,
    KnowledgeRepository,
    TeamRepository,
    ToolDefinitionRepository,
    WorkflowRepository,
)
from app.platform.tenant_import import (
    collect_import_bundle,
    materialize_import_bundle,
)
from app.tenancy.context import TenantContext


@pytest.fixture
def platform_admin(tenant_a) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_a.tenant_id,
        user_id="platform-owner",
        role=Role.platform_admin,
        clerk_org_id=tenant_a.clerk_org_id,
    )


async def _source_graph(session, tenant_a):
    session.info["tenant_id"] = tenant_a.tenant_id
    knowledge = KnowledgeRepository(session, tenant_a)
    base = await knowledge.create_base(name="Policies", config={"locale": "en"})
    await knowledge.create_source(
        knowledge_base_id=base.id,
        kind="upload",
        uri="s3://bucket/policies.pdf",
        metadata={"filename": "policies.pdf"},
    )

    tools = ToolDefinitionRepository(session, tenant_a)
    tool = await tools.create(
        {
            "name": "Lookup",
            "slug": "lookup",
            "description": "HTTP lookup",
            "kind": "http",
            "http_method": "GET",
            "base_url": "https://example.com",
            "path": "/items",
            "request_schema": {},
            "response_description": None,
            "response_schema": None,
            "headers": {},
            "config": {},
            "credential_id": None,
            "approval_required": False,
            "active": True,
        }
    )

    agents = AgentRepository(session, tenant_a)
    agent = await agents.create_config(slug="concierge", name="Concierge")
    await agents.create_draft(
        config_id=agent.id,
        instructions="Help customers",
        model_id="openai:gpt-4.1-mini",
        temperature=0.2,
        tools=[
            {
                "tool_definition_id": tool.id,
                "config": {},
                "credential_id": None,
            }
        ],
        knowledge_base_id=base.id,
    )
    await agents.publish((await agents.get_latest_draft(agent.id)).id)

    teams = TeamRepository(session, tenant_a)
    team = await teams.create_config(slug="frontline", name="Frontline")
    await teams.create_draft(
        config_id=team.id,
        instructions="Coordinate",
        mode="coordinate",
        model_id="openai:gpt-4.1-mini",
        temperature=0.2,
        member_config_ids=[agent.id],
        tools=[],
    )
    await teams.publish((await teams.get_latest_draft(team.id)).id)

    workflows = WorkflowRepository(session, tenant_a)
    workflow = await workflows.create_config(slug="intake", name="Intake")
    await workflows.create_draft(
        config_id=workflow.id,
        mode="sequential",
        steps=[
            {
                "name": "Intake team",
                "target_type": "team",
                "target_config_id": team.id,
            }
        ],
    )
    await workflows.publish((await workflows.get_latest_draft(workflow.id)).id)
    return agent, team, workflow, tool, base


@pytest.mark.asyncio
async def test_import_copies_graph_without_credentials(
    session, tenant_a, tenant_b, platform_admin
):
    agent, team, workflow, tool, base = await _source_graph(session, tenant_a)

    session.info["tenant_id"] = tenant_a.tenant_id
    bundle = await collect_import_bundle(
        session,
        tenant_a,
        team_ids=[],
        workflow_ids=[workflow.id],
    )
    assert agent.id in bundle.agents
    assert team.id in bundle.teams
    assert workflow.id in bundle.workflows
    assert tool.id in bundle.tools
    assert base.id in bundle.knowledge_bases

    session.info["tenant_id"] = tenant_b.tenant_id
    dest_context = TenantContext(
        tenant_id=tenant_b.tenant_id,
        user_id=platform_admin.user_id,
        role=Role.platform_admin,
        clerk_org_id=tenant_b.clerk_org_id,
    )
    result = await materialize_import_bundle(session, dest_context, bundle)

    assert len(result.workflows) == 1
    assert len(result.teams) == 1
    assert len(result.agents) == 1
    assert len(result.tools) == 1
    assert len(result.knowledge_bases) == 1

    dest_workflow_id = uuid.UUID(result.workflows[str(workflow.id)])
    dest_workflows = WorkflowRepository(session, dest_context)
    dest_workflow = await dest_workflows.get_config(dest_workflow_id)
    assert dest_workflow is not None
    assert dest_workflow.slug == "intake"
    assert dest_workflow.published_version_id is None
    draft = await dest_workflows.get_latest_draft(dest_workflow.id)
    assert draft is not None
    steps = await dest_workflows.steps(draft.id)
    assert len(steps) == 1
    assert steps[0].team_config_id == uuid.UUID(result.teams[str(team.id)])

    dest_tools = ToolDefinitionRepository(session, dest_context)
    dest_tool = await dest_tools.get(uuid.UUID(result.tools[str(tool.id)]))
    assert dest_tool is not None
    assert dest_tool.credential_id is None
    assert dest_tool.id != tool.id

    # Source still intact and not visible under dest context
    assert await dest_workflows.get_config(workflow.id) is None
    session.info["tenant_id"] = tenant_a.tenant_id
    assert await WorkflowRepository(session, tenant_a).get_config(workflow.id) is not None


@pytest.mark.asyncio
async def test_same_tenant_import_rejected():
    with pytest.raises(ValidationError):
        TenantImportIn(
            source_tenant_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
            destination_tenant_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
            workflow_ids=[uuid.uuid4()],
        )


@pytest.mark.asyncio
async def test_tenant_admin_cannot_call_import_endpoints(tenant_a):
    dependency = require_roles(Role.platform_admin)
    with pytest.raises(HTTPException) as error:
        await dependency(tenant_a)
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_import_endpoint_writes_audit(
    session, tenant_a, tenant_b, platform_admin, monkeypatch
):
    _, _, workflow, _, _ = await _source_graph(session, tenant_a)
    source = await session.get(Tenant, tenant_a.tenant_id)
    destination = await session.get(Tenant, tenant_b.tenant_id)
    assert source is not None and destination is not None

    async def fake_rls(tenant_id: uuid.UUID):
        session.info["tenant_id"] = tenant_id
        yield session

    monkeypatch.setattr("app.api.platform._tenant_rls_session", fake_rls)

    result = await import_tenant_resources(
        TenantImportIn(
            source_tenant_id=source.id,
            destination_tenant_id=destination.id,
            workflow_ids=[workflow.id],
        ),
        platform_admin,
        session,
    )
    assert result.counts["workflows"] == 1

    events = (
        await session.scalars(
            select(PlatformAuditEvent)
            .where(PlatformAuditEvent.action == "tenant.import")
            .order_by(PlatformAuditEvent.created_at.desc())
        )
    ).all()
    assert events
    assert events[0].tenant_id == destination.id
    assert events[0].actor_id == "platform-owner"


@pytest.mark.asyncio
async def test_catalog_lists_source_teams_and_workflows(
    session, tenant_a, platform_admin, monkeypatch
):
    _, team, workflow, _, _ = await _source_graph(session, tenant_a)
    source = await session.get(Tenant, tenant_a.tenant_id)
    assert source is not None

    async def fake_rls(tenant_id: uuid.UUID):
        session.info["tenant_id"] = tenant_id
        yield session

    monkeypatch.setattr("app.api.platform._tenant_rls_session", fake_rls)
    items = await get_tenant_catalog(source.id, platform_admin, session)
    ids = {item.id for item in items}
    assert team.id in ids
    assert workflow.id in ids
    assert all(item.kind in {"team", "workflow"} for item in items)
