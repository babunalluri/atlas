import pytest
from pydantic import ValidationError

from app.agent_runtime.factory import AgentFactoryService, TeamFactoryService, TeamRuntimeRequest
from app.api.schemas import TeamCreateIn
from app.db.repositories import AgentRepository, TeamRepository


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
async def test_team_repository_rejects_cross_tenant_member(session, tenant_a, tenant_b):
    own, _ = await _published_agent(session, tenant_a, "own")
    other, _ = await _published_agent(session, tenant_b, "other")

    session.info["tenant_id"] = tenant_a.tenant_id
    repo = TeamRepository(session, tenant_a)
    team = await repo.create_config(slug="support", name="Support")
    with pytest.raises(LookupError, match="not found for tenant"):
        await repo.create_draft(
            config_id=team.id,
            instructions="Coordinate",
            mode="coordinate",
            model_id="openai:gpt-4.1-mini",
            temperature=0.2,
            member_config_ids=[own.id, other.id],
        )


@pytest.mark.asyncio
async def test_publish_pins_published_agent_versions(session, tenant_a):
    first, first_v1 = await _published_agent(session, tenant_a, "billing")
    second, _ = await _published_agent(session, tenant_a, "technical")
    agents = AgentRepository(session, tenant_a)
    newer_draft = await agents.create_draft(
        config_id=first.id,
        instructions="Unpublished billing changes",
        model_id="openai:gpt-4.1-mini",
        temperature=0.3,
    )

    teams = TeamRepository(session, tenant_a)
    config = await teams.create_config(slug="help-desk", name="Help desk")
    draft = await teams.create_draft(
        config_id=config.id,
        instructions="Route requests",
        mode="route",
        model_id="openai:gpt-4.1-mini",
        temperature=0.1,
        member_config_ids=[first.id, second.id],
    )
    assert (await teams.members(draft.id))[0].agent_version_id == newer_draft.id

    await teams.publish(draft.id)
    members = await teams.members(draft.id)
    assert members[0].agent_version_id == first_v1.id
    assert (await teams.get_config(config.id)).published_version_id == draft.id


@pytest.mark.asyncio
async def test_team_factory_builds_persisted_ordered_team(session, tenant_a, monkeypatch):
    first, _ = await _published_agent(session, tenant_a, "sales")
    second, _ = await _published_agent(session, tenant_a, "support")
    repo = TeamRepository(session, tenant_a)
    config = await repo.create_config(slug="front-desk", name="Front desk")
    version = await repo.create_draft(
        config_id=config.id,
        instructions="Choose the right specialist",
        mode="route",
        model_id="openai:gpt-4.1-mini",
        temperature=0.2,
        member_config_ids=[second.id, first.id],
    )
    await repo.publish(version.id)

    class FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeTeam:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.id = kwargs["id"]

    class FakeModel:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr("app.agent_runtime.factory.Agent", FakeAgent)
    monkeypatch.setattr("app.agent_runtime.factory.Team", FakeTeam)
    monkeypatch.setattr("app.agent_runtime.factory.OpenAIChat", FakeModel)

    factory = TeamFactoryService(AgentFactoryService(session, tenant_a, allowed_hosts=set()))
    team = await factory.create(
        TeamRuntimeRequest(version_id=version.id, session_id="team-session")
    )
    assert team.kwargs["mode"] == "route"
    assert [member.kwargs["id"] for member in team.kwargs["members"]] == [
        str(second.id),
        str(first.id),
    ]
    assert team._saas_metadata["team_version_id"] == str(version.id)


def test_team_schema_rejects_unknown_mode():
    with pytest.raises(ValidationError):
        TeamCreateIn(slug="bad", name="Bad", mode="broadcast")  # type: ignore[arg-type]
