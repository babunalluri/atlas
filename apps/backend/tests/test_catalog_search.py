import pytest

from app.db.repositories import AgentRepository, TeamRepository, WorkflowRepository


async def _agent(session, tenant, slug: str, *, publish: bool = False):
    session.info["tenant_id"] = tenant.tenant_id
    repo = AgentRepository(session, tenant)
    config = await repo.create_config(slug=slug, name=slug.replace("-", " ").title())
    version = await repo.create_draft(
        config_id=config.id,
        instructions=f"Handle {slug}",
        model_id="openai:gpt-4.1-mini",
        temperature=0.2,
    )
    if publish:
        await repo.publish(version.id)
    return config


@pytest.mark.asyncio
async def test_agent_search_configs_filters_and_pages(session, tenant_a):
    await _agent(session, tenant_a, "claims-navigator", publish=True)
    await _agent(session, tenant_a, "claims-draft")
    await _agent(session, tenant_a, "billing-helper", publish=True)

    repo = AgentRepository(session, tenant_a)
    page, total = await repo.search_configs(q="claims", page=1, page_size=10)
    assert total == 2
    assert {row.slug for row in page} == {"claims-navigator", "claims-draft"}

    published, published_total = await repo.search_configs(
        status="published", page=1, page_size=10
    )
    assert published_total == 2
    assert all(row.published_version_id is not None for row in published)

    drafts, draft_total = await repo.search_configs(status="draft", page=1, page_size=10)
    assert draft_total == 1
    assert drafts[0].slug == "claims-draft"

    first, total_all = await repo.search_configs(page=1, page_size=2)
    second, _ = await repo.search_configs(page=2, page_size=2)
    assert total_all == 3
    assert len(first) == 2
    assert len(second) == 1
    assert {row.id for row in first}.isdisjoint({row.id for row in second})


@pytest.mark.asyncio
async def test_team_and_workflow_search_configs(session, tenant_a):
    session.info["tenant_id"] = tenant_a.tenant_id
    teams = TeamRepository(session, tenant_a)
    workflows = WorkflowRepository(session, tenant_a)
    await teams.create_config(slug="success-desk", name="Success Desk")
    await teams.create_config(slug="ops-room", name="Ops Room")
    await workflows.create_config(slug="onboarding-flow", name="Onboarding Flow")
    await workflows.create_config(slug="renewal-flow", name="Renewal Flow")

    team_page, team_total = await teams.search_configs(q="success", page=1, page_size=10)
    assert team_total == 1
    assert team_page[0].slug == "success-desk"

    workflow_page, workflow_total = await workflows.search_configs(
        q="flow", page=1, page_size=1
    )
    assert workflow_total == 2
    assert len(workflow_page) == 1
