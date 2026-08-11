import pytest
from pydantic import ValidationError

from app.agent_runtime.factory import AgentFactoryService, TeamFactoryService, TeamRuntimeRequest
from app.api.schemas import TeamCreateIn
from app.db.models import AgentStatus
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


@pytest.mark.asyncio
async def test_list_versions_and_restore_published_pointer(session, tenant_a):
    first, _ = await _published_agent(session, tenant_a, "alpha")
    second, _ = await _published_agent(session, tenant_a, "beta")
    teams = TeamRepository(session, tenant_a)
    config = await teams.create_config(slug="history", name="History")

    v1 = await teams.create_draft(
        config_id=config.id,
        instructions="Version one",
        mode="coordinate",
        model_id="openai:gpt-4.1-mini",
        temperature=0.1,
        member_config_ids=[first.id, second.id],
    )
    await teams.publish(v1.id)

    v2 = await teams.create_draft(
        config_id=config.id,
        instructions="Version two",
        mode="route",
        model_id="openai:gpt-4.1-mini",
        temperature=0.2,
        member_config_ids=[second.id, first.id],
    )
    await teams.publish(v2.id)

    versions = list(await teams.list_versions(config.id))
    assert [row.version for row in versions] == [2, 1]
    assert (await teams.get_config(config.id)).published_version_id == v2.id

    restored = await teams.restore_version(config.id, v1.id)
    assert restored.id == v1.id
    assert restored.instructions == "Version one"
    refreshed = await teams.get_config(config.id)
    assert refreshed is not None
    assert refreshed.published_version_id == v1.id

    # Historical published snapshot is unchanged.
    still_v2 = await teams.get_version(v2.id, allow_draft=False)
    assert still_v2 is not None
    assert still_v2.instructions == "Version two"


@pytest.mark.asyncio
async def test_team_tool_bindings_persist_and_factory_attaches(session, tenant_a, monkeypatch):
    first, _ = await _published_agent(session, tenant_a, "ops")
    second, _ = await _published_agent(session, tenant_a, "finance")
    repo = TeamRepository(session, tenant_a)
    config = await repo.create_config(slug="tooling", name="Tooling")
    version = await repo.create_draft(
        config_id=config.id,
        instructions="Use tools when needed",
        mode="coordinate",
        model_id="openai:gpt-4.1-mini",
        temperature=0.2,
        member_config_ids=[first.id, second.id],
        tools=[{"tool_key": "web_search", "config": {"max_results": 3}}],
    )
    bindings = await repo.bindings(version.id)
    assert len(bindings) == 1
    assert bindings[0].tool_key == "web_search"
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

    async def fake_build_tool(self, binding):
        return {"name": binding.tool_key, "config": binding.config}

    monkeypatch.setattr("app.agent_runtime.factory.Agent", FakeAgent)
    monkeypatch.setattr("app.agent_runtime.factory.Team", FakeTeam)
    monkeypatch.setattr("app.agent_runtime.factory.OpenAIChat", FakeModel)
    monkeypatch.setattr(AgentFactoryService, "_build_tool", fake_build_tool)

    factory = TeamFactoryService(AgentFactoryService(session, tenant_a, allowed_hosts=set()))
    team = await factory.create(
        TeamRuntimeRequest(version_id=version.id, session_id="team-tools-session")
    )
    assert team.kwargs["tools"] == [{"name": "web_search", "config": {"max_results": 3}}]


@pytest.mark.asyncio
async def test_leader_only_team_publish_and_factory(session, tenant_a, monkeypatch):
    repo = TeamRepository(session, tenant_a)
    config = await repo.create_config(slug="leader-only", name="Leader only")
    version = await repo.create_draft(
        config_id=config.id,
        instructions="Call tools directly",
        mode="coordinate",
        model_id="openai:gpt-4.1-mini",
        temperature=0.2,
        member_config_ids=[],
        tools=[{"tool_key": "web_search", "config": {"max_results": 2}}],
    )
    await repo.publish(version.id)
    assert (await repo.get_config(config.id)).published_version_id == version.id
    assert list(await repo.members(version.id)) == []

    class FakeTeam:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.id = kwargs["id"]

    class FakeModel:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    async def fake_build_tool(self, binding):
        return {"name": binding.tool_key, "config": binding.config}

    monkeypatch.setattr("app.agent_runtime.factory.Team", FakeTeam)
    monkeypatch.setattr("app.agent_runtime.factory.OpenAIChat", FakeModel)
    monkeypatch.setattr(AgentFactoryService, "_build_tool", fake_build_tool)

    factory = TeamFactoryService(AgentFactoryService(session, tenant_a, allowed_hosts=set()))
    team = await factory.create(
        TeamRuntimeRequest(version_id=version.id, session_id="leader-only-session")
    )
    assert team.kwargs["members"] == []
    assert team.kwargs["tools"] == [{"name": "web_search", "config": {"max_results": 2}}]


@pytest.mark.asyncio
async def test_restore_as_draft_clones_new_version(session, tenant_a):
    first, _ = await _published_agent(session, tenant_a, "gamma")
    second, _ = await _published_agent(session, tenant_a, "delta")
    teams = TeamRepository(session, tenant_a)
    config = await teams.create_config(slug="clone-me", name="Clone me")
    v1 = await teams.create_draft(
        config_id=config.id,
        instructions="Keep this text",
        mode="route",
        model_id="openai:gpt-4.1-mini",
        temperature=0.4,
        member_config_ids=[first.id, second.id],
    )
    await teams.publish(v1.id)
    v2 = await teams.create_draft(
        config_id=config.id,
        instructions="Later changes",
        mode="coordinate",
        model_id="openai:gpt-4.1-mini",
        temperature=0.1,
        member_config_ids=[first.id, second.id],
    )
    await teams.publish(v2.id)

    draft = await teams.restore_version(config.id, v1.id, as_draft=True)
    assert draft.status == AgentStatus.draft
    assert draft.version == 3
    assert draft.instructions == "Keep this text"
    assert draft.mode == "route"
    # Live pointer stays on v2 until the new draft is published.
    assert (await teams.get_config(config.id)).published_version_id == v2.id


@pytest.mark.asyncio
async def test_restore_version_tenant_isolation(session, tenant_a, tenant_b):
    first, _ = await _published_agent(session, tenant_a, "own-a")
    second, _ = await _published_agent(session, tenant_a, "own-b")
    teams_a = TeamRepository(session, tenant_a)
    config = await teams_a.create_config(slug="private", name="Private")
    version = await teams_a.create_draft(
        config_id=config.id,
        instructions="Secret",
        mode="coordinate",
        model_id="openai:gpt-4.1-mini",
        temperature=0.2,
        member_config_ids=[first.id, second.id],
    )
    await teams_a.publish(version.id)

    session.info["tenant_id"] = tenant_b.tenant_id
    teams_b = TeamRepository(session, tenant_b)
    assert list(await teams_b.list_versions(config.id)) == []
    with pytest.raises(LookupError, match="Team not found"):
        await teams_b.restore_version(config.id, version.id)
