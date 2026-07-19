import uuid

import pytest
from agno.models.response import ToolExecution
from agno.run.agent import RunOutput
from agno.run.requirement import RunRequirement
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_runtime.persistence import runtime_session_id, runtime_user_id
from app.api.approvals import resolve_approval
from app.api.schemas import ApprovalResolveIn
from app.auth.service_accounts import (
    authenticate_service_account,
    hash_service_account_token,
    mint_service_account_token,
)
from app.db.models import KnowledgeChunk
from app.db.repositories import (
    AgentRepository,
    ApprovalRepository,
    KnowledgeRepository,
    ServiceAccountRepository,
    SessionRepository,
)
from app.knowledge.embeddings import EmbeddingService
from app.knowledge.store import TenantKnowledgeStore


async def _published_agent(session, context, slug):
    repo = AgentRepository(session, context)
    config = await repo.create_config(slug=slug, name=slug.title())
    version = await repo.create_draft(
        config_id=config.id,
        instructions="Be useful",
        model_id="openai:gpt-4.1-mini",
        temperature=0.1,
    )
    await repo.publish(version.id)
    return config, version


@pytest.mark.asyncio
async def test_session_pin_is_immutable_and_user_scoped(session, tenant_a):
    session.info["tenant_id"] = tenant_a.tenant_id
    config, version = await _published_agent(session, tenant_a, "durable")
    repo = SessionRepository(session, tenant_a)
    first = await repo.pin(
        external_session_id="browser-session",
        agent_config_id=config.id,
        agent_version_id=version.id,
        runtime_session_id=runtime_session_id(tenant_a, "browser-session"),
        runtime_user_id=runtime_user_id(tenant_a),
    )
    assert (
        await repo.pin(
            external_session_id="browser-session",
            agent_config_id=config.id,
            agent_version_id=version.id,
            runtime_session_id=runtime_session_id(tenant_a, "browser-session"),
            runtime_user_id=runtime_user_id(tenant_a),
        )
    ).id == first.id
    assert first.agent_version_id == version.id
    assert str(tenant_a.tenant_id) in first.runtime_session_id
    assert tenant_a.user_id in first.runtime_user_id


@pytest.mark.asyncio
async def test_approval_bridge_is_idempotent_and_audited(session, tenant_a):
    session.info["tenant_id"] = tenant_a.tenant_id
    config, version = await _published_agent(session, tenant_a, "approval")
    conversation = await SessionRepository(session, tenant_a).pin(
        external_session_id="approval-session",
        agent_config_id=config.id,
        agent_version_id=version.id,
        runtime_session_id=runtime_session_id(tenant_a, "approval-session"),
        runtime_user_id=runtime_user_id(tenant_a),
    )
    requirement = {
        "id": "requirement-1",
        "tool_execution": {
            "tool_call_id": "call-1",
            "tool_name": "delete_record",
            "tool_args": {"record_id": "safe-preview"},
            "requires_confirmation": True,
        },
    }
    repo = ApprovalRepository(session, tenant_a)
    first = await repo.create_from_requirement(
        conversation=conversation,
        run_id="run-1",
        requirement=requirement,
    )
    duplicate = await repo.create_from_requirement(
        conversation=conversation,
        run_id="run-1",
        requirement=requirement,
    )
    assert duplicate.id == first.id
    resolved = await repo.resolve(first.id, False, "Outside approved scope")
    assert resolved is not None
    assert resolved.decision_reason == "Outside approved scope"
    assert await repo.resolve(first.id, True) is None


@pytest.mark.asyncio
async def test_approval_resolution_continues_exact_mocked_run(session, tenant_a, monkeypatch):
    session.info["tenant_id"] = tenant_a.tenant_id
    config, version = await _published_agent(session, tenant_a, "continue")
    conversation = await SessionRepository(session, tenant_a).pin(
        external_session_id="continue-session",
        agent_config_id=config.id,
        agent_version_id=version.id,
        runtime_session_id=runtime_session_id(tenant_a, "continue-session"),
        runtime_user_id=runtime_user_id(tenant_a),
    )
    requirement = RunRequirement(
        ToolExecution(
            tool_call_id="call-continue",
            tool_name="delete_record",
            tool_args={"record_id": "42"},
            requires_confirmation=True,
        ),
        id="requirement-continue",
    )
    approval = await ApprovalRepository(session, tenant_a).create_from_requirement(
        conversation=conversation,
        run_id="run-continue",
        requirement=requirement.to_dict(),
    )
    continued = False

    class FakeComponent:
        async def aget_run_output(self, **kwargs):
            assert kwargs["run_id"] == "run-continue"
            return RunOutput(
                run_id="run-continue",
                session_id=conversation.runtime_session_id,
                requirements=[requirement],
            )

        async def acontinue_run(self, **kwargs):
            nonlocal continued
            continued = True
            assert kwargs["run_response"].run_id == "run-continue"
            assert kwargs["requirements"][0].confirmation is True
            return kwargs["run_response"]

    async def fake_create(*args, **kwargs):
        return FakeComponent()

    monkeypatch.setattr(
        "app.api.approvals.AgentFactoryService.create",
        fake_create,
    )
    result = await resolve_approval(
        approval.id,
        ApprovalResolveIn(approved=True, reason="Approved test scope"),
        tenant_a,
        session,
    )
    assert continued is True
    assert result.status == "approved"


@pytest.mark.asyncio
async def test_rag_ranking_and_tenant_isolation(session, tenant_a, tenant_b):
    async def fake_embed(texts):
        return [
            [1.0, 0.0, *([0.0] * 1534)]
            if "billing" in text.lower()
            else [0.0, 1.0, *([0.0] * 1534)]
            for text in texts
        ]

    embedder = EmbeddingService(api_key=None, embed_callable=fake_embed)
    query_vector = (await embedder.embed(["billing refund question"]))[0]

    session.info["tenant_id"] = tenant_a.tenant_id
    repo_a = KnowledgeRepository(session, tenant_a)
    base_a = await repo_a.create_base(name="Tenant A")
    source_a = await repo_a.create_source(
        knowledge_base_id=base_a.id,
        kind="test",
        uri="tenant-a.txt",
    )
    session.add_all(
        [
            KnowledgeChunk(
                id=uuid.uuid4(),
                tenant_id=tenant_a.tenant_id,
                knowledge_base_id=base_a.id,
                source_id=source_a.id,
                content="Billing refunds are available for 30 days.",
                embedding=[1.0, 0.0, *([0.0] * 1534)],
                content_hash="a" * 64,
                metadata_={"filename": "billing.txt"},
            ),
            KnowledgeChunk(
                id=uuid.uuid4(),
                tenant_id=tenant_a.tenant_id,
                knowledge_base_id=base_a.id,
                source_id=source_a.id,
                content="The office kitchen closes at five.",
                embedding=[0.0, 1.0, *([0.0] * 1534)],
                content_hash="b" * 64,
                metadata_={"filename": "office.txt"},
            ),
        ]
    )
    await session.flush()
    results = await TenantKnowledgeStore(session, tenant_a).search(
        base_a.id,
        "billing refund question",
        query_vector,
        score_threshold=0.1,
    )
    assert results[0]["content"].startswith("Billing refunds")

    session.info["tenant_id"] = tenant_b.tenant_id
    results_for_other_tenant = await TenantKnowledgeStore(session, tenant_b).search(
        base_a.id,
        "billing refund question",
        query_vector,
        score_threshold=0,
    )
    assert results_for_other_tenant == []


@pytest.mark.asyncio
async def test_service_account_token_is_hashed_scoped_and_revocable(
    session, tenant_a, tenant_b, monkeypatch
):
    session.info["tenant_id"] = tenant_a.tenant_id
    minted = mint_service_account_token(tenant_a.tenant_id)
    repo = ServiceAccountRepository(session, tenant_a)
    account = await repo.create(
        name="CI runner",
        token_prefix=minted.token_prefix,
        token_hash=minted.token_hash,
        scopes=["agents:run", "sessions:read"],
        expires_at=None,
    )
    assert account.token_hash == hash_service_account_token(minted.token)
    assert minted.token not in account.token_hash
    await session.commit()

    factory = async_sessionmaker(session.bind, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr("app.auth.service_accounts.SessionFactory", factory)
    context = await authenticate_service_account(minted.token)
    assert context is not None
    assert context.tenant_id == tenant_a.tenant_id
    assert context.user_id == f"sa:{account.id}"
    assert context.has_scope("agents:run")
    assert not context.has_scope("teams:run")

    session.info["tenant_id"] = tenant_b.tenant_id
    assert await ServiceAccountRepository(session, tenant_b).list_accounts() == []
    session.info["tenant_id"] = tenant_a.tenant_id
    assert await repo.revoke(account.id) is not None
    await session.commit()
    assert await authenticate_service_account(minted.token) is None
