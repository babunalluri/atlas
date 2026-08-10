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
async def test_factory_attaches_guardrail_pre_hooks(session, tenant_a, monkeypatch):
    session.info["tenant_id"] = tenant_a.tenant_id
    repo = AgentRepository(session, tenant_a)
    config = await repo.create_config(slug="guarded", name="Guarded")
    version = await repo.create_draft(
        config_id=config.id,
        instructions="Be careful",
        model_id="openai:gpt-4.1-mini",
        temperature=0.1,
        guardrails={
            "prompt_injection": True,
            "pii_detection": True,
            "openai_moderation": False,
        },
    )
    await session.commit()

    captured: dict = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeModel:
        def __init__(self, **kwargs):
            captured["model_kwargs"] = kwargs

    class FakePrompt:
        pass

    class FakePii:
        pass

    monkeypatch.setattr("app.agent_runtime.factory.Agent", FakeAgent)
    monkeypatch.setattr("app.agent_runtime.factory.OpenAIChat", FakeModel)
    monkeypatch.setattr(
        "agno.guardrails.PromptInjectionGuardrail", FakePrompt, raising=False
    )
    monkeypatch.setattr(
        "agno.guardrails.PIIDetectionGuardrail", FakePii, raising=False
    )

    # Patch the imports used inside helper by stubbing modules after import path.
    import app.agent_runtime.factory as factory_mod

    def fake_hooks(team_config):
        raw = (team_config or {}).get("guardrails") or {}
        hooks = []
        if raw.get("prompt_injection"):
            hooks.append(FakePrompt())
        if raw.get("pii_detection"):
            hooks.append(FakePii())
        return hooks

    monkeypatch.setattr(factory_mod, "_guardrails_from_team_config", fake_hooks)

    factory = AgentFactoryService(session, tenant_a, allowed_hosts=set())
    agent = await factory.create(
        RuntimeRequest(version_id=version.id, session_id="sess-guard", preview=True)
    )
    assert agent is not None
    assert len(captured["pre_hooks"]) == 2
    assert version.team_config["guardrails"]["prompt_injection"] is True


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
        auth_org_id=tenant_a.auth_org_id,
    )
    factory = AgentFactoryService(session, end_user, allowed_hosts=set())
    with pytest.raises(LookupError):
        await factory.create(RuntimeRequest(version_id=draft.id, session_id="s", preview=True))


@pytest.mark.asyncio
async def test_factory_builds_moonshot_kimi_model(session, tenant_a, monkeypatch):
    session.info["tenant_id"] = tenant_a.tenant_id
    repo = AgentRepository(session, tenant_a)
    config = await repo.create_config(slug="kimi-bot", name="Kimi Bot")
    version = await repo.create_draft(
        config_id=config.id,
        instructions="hi",
        model_id="moonshot:kimi-k2.5",
        temperature=0.3,
    )
    await session.commit()

    captured: dict = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeMoonShot:
        def __init__(self, **kwargs):
            captured["model_kwargs"] = kwargs

    monkeypatch.setattr("app.agent_runtime.factory.Agent", FakeAgent)
    monkeypatch.setattr("app.agent_runtime.factory.OpenAIChat", FakeAgent)
    monkeypatch.setattr("app.agent_runtime.factory.MoonShot", FakeMoonShot)
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-moonshot-test")

    factory = AgentFactoryService(session, tenant_a, allowed_hosts=set())
    await factory.create(
        RuntimeRequest(version_id=version.id, session_id="s-kimi", preview=True)
    )
    assert captured["model_kwargs"]["id"] == ALLOWED_MODELS["moonshot:kimi-k2.5"]
    assert captured["model_kwargs"]["api_key"] == "sk-moonshot-test"


@pytest.mark.asyncio
async def test_factory_builds_nvidia_model(session, tenant_a, monkeypatch):
    session.info["tenant_id"] = tenant_a.tenant_id
    repo = AgentRepository(session, tenant_a)
    config = await repo.create_config(slug="nvidia-bot", name="NVIDIA Bot")
    version = await repo.create_draft(
        config_id=config.id,
        instructions="hi",
        model_id="nvidia:nvidia-llama-3.3-70b",
        temperature=0.2,
    )
    await session.commit()

    captured: dict = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeNvidia:
        def __init__(self, **kwargs):
            captured["model_kwargs"] = kwargs

    monkeypatch.setattr("app.agent_runtime.factory.Agent", FakeAgent)
    monkeypatch.setattr("app.agent_runtime.factory.OpenAIChat", FakeAgent)
    monkeypatch.setattr("app.agent_runtime.factory.Nvidia", FakeNvidia)
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")

    factory = AgentFactoryService(session, tenant_a, allowed_hosts=set())
    await factory.create(
        RuntimeRequest(version_id=version.id, session_id="s-nvidia", preview=True)
    )
    assert (
        captured["model_kwargs"]["id"]
        == ALLOWED_MODELS["nvidia:nvidia-llama-3.3-70b"]
    )
    assert captured["model_kwargs"]["api_key"] == "nvapi-test"


@pytest.mark.asyncio
async def test_factory_builds_gemini_model(session, tenant_a, monkeypatch):
    session.info["tenant_id"] = tenant_a.tenant_id
    repo = AgentRepository(session, tenant_a)
    config = await repo.create_config(slug="gemini-bot", name="Gemini Bot")
    version = await repo.create_draft(
        config_id=config.id,
        instructions="hi",
        model_id="gemini:gemini-2.5-flash",
        temperature=0.2,
    )
    await session.commit()

    captured: dict = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeGemini:
        def __init__(self, **kwargs):
            captured["model_kwargs"] = kwargs

    monkeypatch.setattr("app.agent_runtime.factory.Agent", FakeAgent)
    monkeypatch.setattr("app.agent_runtime.factory.OpenAIChat", FakeAgent)
    monkeypatch.setattr("app.agent_runtime.factory.Gemini", FakeGemini)
    monkeypatch.setenv("GOOGLE_API_KEY", "google-test-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    from app.core import settings as settings_mod

    settings_mod.get_settings.cache_clear()

    factory = AgentFactoryService(session, tenant_a, allowed_hosts=set())
    await factory.create(
        RuntimeRequest(version_id=version.id, session_id="s-gemini", preview=True)
    )
    assert (
        captured["model_kwargs"]["id"] == ALLOWED_MODELS["gemini:gemini-2.5-flash"]
    )
    assert captured["model_kwargs"]["api_key"] == "google-test-key"
    settings_mod.get_settings.cache_clear()
