import pytest

from app.agent_runtime.factory import ALLOWED_MODELS, AgentFactoryService, RuntimeRequest
from app.db.repositories import AgentRepository


@pytest.mark.asyncio
async def test_factory_rejects_unknown_model(session, tenant_a, monkeypatch):
    session.info["tenant_id"] = tenant_a.tenant_id
    repo = AgentRepository(session, tenant_a)
    config = await repo.create_config(slug="bot", name="Bot")
    version = await repo.create_draft(
        config_id=config.id,
        instructions="hi",
        model_id="openai:gpt-4.1-mini",
        temperature=0.2,
    )
    # Force a bad model after draft creation.
    version.model_id = "openai:not-allowed"
    await session.commit()

    class FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeModel:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr("app.agent_runtime.factory.Agent", FakeAgent)
    monkeypatch.setattr("app.agent_runtime.factory.OpenAIChat", FakeModel)

    factory = AgentFactoryService(session, tenant_a, allowed_hosts=set())
    with pytest.raises(ValueError, match="allowlisted"):
        await factory.create(RuntimeRequest(version_id=version.id, session_id="s1", preview=True))


@pytest.mark.asyncio
async def test_factory_builds_agent_for_tenant(session, tenant_a, monkeypatch):
    session.info["tenant_id"] = tenant_a.tenant_id
    repo = AgentRepository(session, tenant_a)
    config = await repo.create_config(slug="helper", name="Helper")
    version = await repo.create_draft(
        config_id=config.id,
        instructions="Be helpful",
        model_id="openai:gpt-4.1-mini",
        temperature=0.1,
        tools=[{"tool_key": "web_search", "config": {}}],
    )
    await repo.publish(version.id)
    await session.commit()

    captured: dict = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeModel:
        def __init__(self, **kwargs):
            captured["model_kwargs"] = kwargs

    monkeypatch.setattr("app.agent_runtime.factory.Agent", FakeAgent)
    monkeypatch.setattr("app.agent_runtime.factory.OpenAIChat", FakeModel)

    factory = AgentFactoryService(session, tenant_a, allowed_hosts=set())
    agent = await factory.create(
        RuntimeRequest(version_id=version.id, session_id="sess-factory", preview=False)
    )
    assert agent is not None
    assert captured["instructions"] == "Be helpful"
    assert captured["model_kwargs"]["id"] == ALLOWED_MODELS["openai:gpt-4.1-mini"]
    assert agent._saas_metadata["tenant_id"] == str(tenant_a.tenant_id)


@pytest.mark.asyncio
async def test_factory_hides_draft_from_end_users(session, tenant_a):
    from app.db.models import Role
    from app.tenancy.context import TenantContext

    session.info["tenant_id"] = tenant_a.tenant_id
    repo = AgentRepository(session, tenant_a)
    config = await repo.create_config(slug="secret", name="Secret")
    draft = await repo.create_draft(
        config_id=config.id,
        instructions="draft only",
        model_id="openai:gpt-4.1-mini",
        temperature=0.2,
    )
    await session.commit()

    end_user = TenantContext(
        tenant_id=tenant_a.tenant_id,
        user_id="end",
        role=Role.end_user,
        clerk_org_id=tenant_a.clerk_org_id,
    )
    factory = AgentFactoryService(session, end_user, allowed_hosts=set())
    with pytest.raises(LookupError):
        await factory.create(RuntimeRequest(version_id=draft.id, session_id="s", preview=True))
