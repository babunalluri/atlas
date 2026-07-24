import pytest

from app.db.repositories import AgentRepository, TeamRepository, WorkflowRepository


async def _published_agent(session, tenant, slug: str):
    session.info["tenant_id"] = tenant.tenant_id
    repo = AgentRepository(session, tenant)
    config = await repo.create_config(slug=slug, name=slug.replace("-", " ").title())
    version = await repo.create_draft(
        config_id=config.id,
        instructions=f"Handle {slug}",
        model_id="openai:gpt-4.1-mini",
        temperature=0.2,
    )
    await repo.publish(version.id)
    return config


async def _published_team(session, tenant, slug: str, member_slugs: list[str]):
    members = [await _published_agent(session, tenant, name) for name in member_slugs]
    teams = TeamRepository(session, tenant)
    config = await teams.create_config(slug=slug, name=slug.replace("-", " ").title())
    draft = await teams.create_draft(
        config_id=config.id,
        instructions="Coordinate",
        mode="coordinate",
        model_id="openai:gpt-4.1-mini",
        temperature=0.2,
        member_config_ids=[member.id for member in members],
    )
    await teams.publish(draft.id)
    return config


@pytest.mark.asyncio
async def test_resolve_published_team_step(session, tenant_a):
    team_a = await _published_team(
        session, tenant_a, "front-line", ["agent-one", "agent-two"]
    )
    team_b = await _published_team(
        session, tenant_a, "back-office", ["agent-three", "agent-four"]
    )
    outsider = await _published_team(
        session, tenant_a, "orphan-team", ["agent-five", "agent-six"]
    )

    workflows = WorkflowRepository(session, tenant_a)
    workflow = await workflows.create_config(slug="onboarding", name="Onboarding")
    draft = await workflows.create_draft(
        config_id=workflow.id,
        mode="sequential",
        steps=[
            {
                "name": "Front line",
                "target_type": "team",
                "target_config_id": team_a.id,
            },
            {
                "name": "Back office",
                "target_type": "team",
                "target_config_id": team_b.id,
            },
        ],
    )
    await workflows.publish(draft.id)

    refreshed_a = await TeamRepository(session, tenant_a).get_config(team_a.id)
    assert refreshed_a is not None and refreshed_a.published_version_id is not None

    pinned = await workflows.resolve_published_team_step(workflow.id, team_a.id)
    assert pinned == refreshed_a.published_version_id

    with pytest.raises(LookupError, match="not a step"):
        await workflows.resolve_published_team_step(workflow.id, outsider.id)

    with pytest.raises(LookupError, match="Published workflow not found"):
        await workflows.resolve_published_team_step(outsider.id, team_a.id)
