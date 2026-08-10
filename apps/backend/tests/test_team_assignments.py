"""Team assignments are scoped like workflow assignments."""

from __future__ import annotations

import pytest

from app.db.repositories import AgentRepository, TeamRepository


async def _published_team(session, tenant, slug: str):
    session.info["tenant_id"] = tenant.tenant_id
    agents = AgentRepository(session, tenant)
    agent = await agents.create_config(slug=f"{slug}-agent", name=f"{slug} agent")
    agent_version = await agents.create_draft(
        config_id=agent.id,
        instructions="help",
        model_id="openai:gpt-4.1-mini",
        temperature=0.1,
    )
    await agents.publish(agent_version.id)

    teams = TeamRepository(session, tenant)
    config = await teams.create_config(slug=slug, name=slug.replace("-", " ").title())
    version = await teams.create_draft(
        config_id=config.id,
        instructions="Coordinate",
        mode="coordinate",
        model_id="openai:gpt-4.1-mini",
        temperature=0.1,
        member_config_ids=[agent.id],
    )
    await teams.publish(version.id)
    await session.commit()
    return config


@pytest.mark.asyncio
async def test_team_assignments_are_user_and_tenant_scoped(session, tenant_a, tenant_b):
    config = await _published_team(session, tenant_a, "assigned-team")
    repo = TeamRepository(session, tenant_a)

    assert await repo.list_available_for_user("customer-one") == []
    assert await repo.replace_user_assignments(
        "customer-one", [config.id, config.id]
    ) == [config.id]
    assert [row.id for row in await repo.list_available_for_user("customer-one")] == [
        config.id
    ]
    assert await repo.is_assigned(config.id, "customer-one")
    assert not await repo.is_assigned(config.id, "unassigned")

    session.info["tenant_id"] = tenant_b.tenant_id
    other = TeamRepository(session, tenant_b)
    assert await other.list_available_for_user("customer-one") == []
    assert not await other.is_assigned(config.id, "customer-one")
