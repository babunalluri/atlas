import uuid

import pytest

from app.credentials.provider import (
    CredentialError,
    EncryptedEnvelope,
    LocalFernetCipher,
    ephemeral_cipher,
)
from app.db.models import (
    AgentStatus,
    ApprovalBinding,
    ApprovalStatus,
    ConversationSession,
)
from app.db.repositories import AgentRepository, ApprovalRepository
from app.observability.tracing import redact
from app.tenancy.context import TenantContext
from app.tenancy.ids import validate_slug


@pytest.mark.asyncio
async def test_agent_repository_scopes_by_tenant(session, tenant_a, tenant_b):
    repo_a = AgentRepository(session, tenant_a)
    session.info["tenant_id"] = tenant_a.tenant_id
    config = await repo_a.create_config(slug="support", name="Support")
    await repo_a.create_draft(
        config_id=config.id,
        instructions="Help Acme users",
        model_id="openai:gpt-4.1-mini",
        temperature=0.1,
    )
    await session.commit()

    session.info["tenant_id"] = tenant_b.tenant_id
    repo_b = AgentRepository(session, tenant_b)
    assert await repo_b.get_config(config.id) is None
    assert list(await repo_b.list_configs()) == []

    session.info["tenant_id"] = tenant_a.tenant_id
    found = await repo_a.get_config(config.id)
    assert found is not None
    assert found.name == "Support"


@pytest.mark.asyncio
async def test_publish_pins_immutable_version(session, tenant_a):
    session.info["tenant_id"] = tenant_a.tenant_id
    repo = AgentRepository(session, tenant_a)
    config = await repo.create_config(slug="ops", name="Ops")
    draft = await repo.create_draft(
        config_id=config.id,
        instructions="v1",
        model_id="openai:gpt-4.1-mini",
        temperature=0.2,
    )
    published = await repo.publish(draft.id)
    assert published.status == AgentStatus.published
    refreshed = await repo.get_config(config.id)
    assert refreshed is not None
    assert refreshed.published_version_id == published.id

    await repo.create_draft(
        config_id=config.id,
        instructions="v2 draft",
        model_id="openai:gpt-4.1-mini",
        temperature=0.3,
    )
    still = await repo.get_version(published.id, allow_draft=False)
    assert still is not None
    assert still.instructions == "v1"


@pytest.mark.asyncio
async def test_agent_list_versions_and_restore_published_pointer(session, tenant_a):
    session.info["tenant_id"] = tenant_a.tenant_id
    repo = AgentRepository(session, tenant_a)
    config = await repo.create_config(slug="history-agent", name="History")
    v1 = await repo.create_draft(
        config_id=config.id,
        instructions="first",
        model_id="openai:gpt-4.1-mini",
        temperature=0.1,
    )
    await repo.publish(v1.id)
    v2 = await repo.create_draft(
        config_id=config.id,
        instructions="second",
        model_id="openai:gpt-4.1-mini",
        temperature=0.2,
    )
    await repo.publish(v2.id)

    versions = list(await repo.list_versions(config.id))
    assert [row.version for row in versions] == [2, 1]
    assert (await repo.get_config(config.id)).published_version_id == v2.id

    restored = await repo.restore_version(config.id, v1.id)
    assert restored.id == v1.id
    assert (await repo.get_config(config.id)).published_version_id == v1.id
    still = await repo.get_version(v2.id, allow_draft=False)
    assert still is not None and still.instructions == "second"


@pytest.mark.asyncio
async def test_agent_create_draft_archives_prior_drafts(session, tenant_a):
    session.info["tenant_id"] = tenant_a.tenant_id
    repo = AgentRepository(session, tenant_a)
    config = await repo.create_config(slug="archive-drafts", name="Archive Drafts")
    v1 = await repo.create_draft(
        config_id=config.id,
        instructions="first save",
        model_id="openai:gpt-4.1-mini",
        temperature=0.2,
        tools=[{"tool_key": "rest_mutate", "config": {}}],
    )
    v2 = await repo.create_draft(
        config_id=config.id,
        instructions="second save",
        model_id="openai:gpt-4.1-mini",
        temperature=0.2,
        tools=[
            {"tool_key": "rest_mutate", "config": {}},
            {"tool_key": "web_search", "config": {}},
        ],
    )
    refreshed_v1 = await repo.get_version(v1.id, allow_draft=True)
    assert refreshed_v1 is not None
    assert refreshed_v1.status == AgentStatus.archived
    latest = await repo.get_latest_draft(config.id)
    assert latest is not None and latest.id == v2.id


@pytest.mark.asyncio
async def test_agent_stale_draft_ignored_after_publish(session, tenant_a):
    """Older leftover drafts must not reopen as the editor source after publish.

    Regression: draft vN-1 stayed ``draft`` after publishing vN, so the agent
    editor showed stale tool bindings (e.g. Freshdesk missing) while live used vN.
    """
    session.info["tenant_id"] = tenant_a.tenant_id
    repo = AgentRepository(session, tenant_a)
    config = await repo.create_config(slug="stale-draft", name="Stale Draft")
    v1 = await repo.create_draft(
        config_id=config.id,
        instructions="first save",
        model_id="openai:gpt-4.1-mini",
        temperature=0.2,
        tools=[{"tool_key": "rest_mutate", "config": {}}],
    )
    v2 = await repo.create_draft(
        config_id=config.id,
        instructions="second save with more tools",
        model_id="openai:gpt-4.1-mini",
        temperature=0.2,
        tools=[
            {"tool_key": "rest_mutate", "config": {}},
            {"tool_key": "web_search", "config": {}},
        ],
    )
    await repo.publish(v2.id)

    # Simulate legacy data: resurrect an older draft that was never archived.
    stale = await repo.get_version(v1.id, allow_draft=True)
    assert stale is not None
    stale.status = AgentStatus.draft
    await session.flush()

    assert await repo.get_latest_draft(config.id) is None
    bindings = list(await repo.bindings(v2.id))
    assert {b.tool_key for b in bindings} == {"rest_mutate", "web_search"}


@pytest.mark.asyncio
async def test_agent_restore_as_draft_clones_tools(session, tenant_a):
    session.info["tenant_id"] = tenant_a.tenant_id
    repo = AgentRepository(session, tenant_a)
    config = await repo.create_config(slug="clone-agent", name="Clone")
    v1 = await repo.create_draft(
        config_id=config.id,
        instructions="with tool",
        model_id="openai:gpt-4.1-mini",
        temperature=0.2,
        tools=[{"tool_key": "web_search", "config": {"max_results": 3}}],
    )
    await repo.publish(v1.id)
    v2 = await repo.create_draft(
        config_id=config.id,
        instructions="without tool",
        model_id="openai:gpt-4.1-mini",
        temperature=0.2,
        tools=[],
    )
    await repo.publish(v2.id)

    draft = await repo.restore_version(config.id, v1.id, as_draft=True)
    assert draft.status == AgentStatus.draft
    assert draft.version == 3
    assert draft.instructions == "with tool"
    bindings = list(await repo.bindings(draft.id))
    assert len(bindings) == 1
    assert bindings[0].tool_key == "web_search"
    assert (await repo.get_config(config.id)).published_version_id == v2.id


@pytest.mark.asyncio
async def test_end_user_cannot_resolve_approvals(session, tenant_a):
    session.info["tenant_id"] = tenant_a.tenant_id
    conv = ConversationSession(
        id=uuid.uuid4(),
        tenant_id=tenant_a.tenant_id,
        external_session_id="sess-1",
        agent_config_id=uuid.uuid4(),
        agent_version_id=uuid.uuid4(),
        user_id="user-a",
    )
    approval = ApprovalBinding(
        id=uuid.uuid4(),
        tenant_id=tenant_a.tenant_id,
        session_id=conv.id,
        tool_name="mutate_rest",
        request_hash="abc",
        redacted_arguments={"path": "/x"},
        status=ApprovalStatus.pending,
    )
    session.add_all([conv, approval])
    await session.commit()

    end_user = TenantContext(
        tenant_id=tenant_a.tenant_id,
        user_id="end",
        role=__import__("app.db.models", fromlist=["Role"]).Role.end_user,
        auth_org_id=tenant_a.auth_org_id,
    )
    repo = ApprovalRepository(session, end_user)
    with pytest.raises(PermissionError):
        await repo.resolve(approval.id, True)


def test_credential_roundtrip_and_redaction():
    cipher = ephemeral_cipher()
    envelope = cipher.encrypt("sk-secret-value")
    assert isinstance(envelope, EncryptedEnvelope)
    assert cipher.decrypt(envelope) == "sk-secret-value"
    assert "sk-secret" not in envelope.ciphertext
    redacted = redact({"Authorization": "Bearer x", "ok": "yes", "api_key": "nope"})
    assert redacted["Authorization"] == "[REDACTED]"
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["ok"] == "yes"


def test_slug_validation():
    assert validate_slug("Acme-Bot") == "acme-bot"
    with pytest.raises(ValueError):
        validate_slug("Bad Slug!")


def test_local_cipher_rejects_empty():
    cipher = LocalFernetCipher("dev-only-change-me-please-32b")
    with pytest.raises(CredentialError):
        cipher.encrypt("")
