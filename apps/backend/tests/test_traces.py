import pytest
from fastapi import HTTPException

from app.api.traces import get_trace, list_traces
from app.db.repositories import AgentRepository, SessionRepository
from app.observability.repository import TraceRepository


async def _conversation(session, context):
    agent_repo = AgentRepository(session, context)
    config = await agent_repo.create_config(slug="traced-agent", name="Traced Agent")
    version = await agent_repo.create_draft(
        config_id=config.id,
        instructions="Be useful",
        model_id="openai:gpt-4.1-mini",
        temperature=0.1,
    )
    await agent_repo.publish(version.id)
    conversation = await SessionRepository(session, context).pin(
        external_session_id="trace-session",
        agent_config_id=config.id,
        agent_version_id=version.id,
        runtime_session_id=f"tenant:{context.tenant_id}:session:trace-session",
        runtime_user_id=f"tenant:{context.tenant_id}:user:{context.user_id}",
    )
    return config, version, conversation


@pytest.mark.asyncio
async def test_trace_event_tree_is_durable_redacted_and_tenant_scoped(session, tenant_a, tenant_b):
    session.info["tenant_id"] = tenant_a.tenant_id
    config, version, conversation = await _conversation(session, tenant_a)
    repo = TraceRepository(session, tenant_a)
    trace = await repo.start(
        conversation=conversation,
        target_id=config.id,
        version_id=version.id,
        name="Agent run",
        message="Trace this request",
        metadata={"authorization": "Bearer secret", "model": "gpt-test"},
    )
    await repo.record_event(
        trace.id,
        {
            "event": "RunStarted",
            "run_id": "run-trace-1",
            "authorization": "Bearer secret",
        },
    )
    await repo.record_event(
        trace.id,
        {
            "event": "RunCompleted",
            "run_id": "run-trace-1",
            "content": "Done",
            "authorization": "Bearer secret",
        },
    )

    detail = await get_trace(trace.id, tenant_a, session)
    assert detail["status"] == "completed"
    assert detail["run_id"] == "run-trace-1"
    assert detail["metadata"]["authorization"] == "[REDACTED]"
    # RunStarted is intentionally not persisted as its own span (noise).
    assert len(detail["spans"]) == 2
    assert detail["spans"][0]["kind"] == "run"
    assert detail["spans"][1]["name"] == "RunCompleted"
    assert detail["spans"][1]["parent_span_id"] == detail["spans"][0]["id"]
    assert detail["spans"][1]["attributes"]["authorization"] == "[REDACTED]"

    session.info["tenant_id"] = tenant_b.tenant_id
    assert await TraceRepository(session, tenant_b).list() == []
    with pytest.raises(HTTPException) as error:
        await get_trace(trace.id, tenant_b, session)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_trace_list_filters_by_status_and_target(session, tenant_a):
    session.info["tenant_id"] = tenant_a.tenant_id
    config, version, conversation = await _conversation(session, tenant_a)
    repo = TraceRepository(session, tenant_a)
    trace = await repo.start(
        conversation=conversation,
        target_id=config.id,
        version_id=version.id,
        name="Agent run",
        message="Hello",
    )
    await repo.record_event(trace.id, {"event": "RunPaused", "run_id": "run-paused"})

    rows = await list_traces(
        tenant_a,
        session,
        status="paused",
        target_type="agent",
        session_id="trace-session",
        limit=20,
    )
    assert [row["id"] for row in rows] == [trace.id]
    assert rows[0]["span_count"] == 2


@pytest.mark.asyncio
async def test_run_completed_preserves_streamed_content(session, tenant_a):
    session.info["tenant_id"] = tenant_a.tenant_id
    config, version, conversation = await _conversation(session, tenant_a)
    repo = TraceRepository(session, tenant_a)
    trace = await repo.start(
        conversation=conversation,
        target_id=config.id,
        version_id=version.id,
        name="Agent run",
        message="Stream then complete",
    )
    await repo.record_event(trace.id, {"event": "RunContent", "content": "Hello "})
    await repo.record_event(trace.id, {"event": "RunContent", "content": "world"})
    # Terminal event often omits content — must not wipe streamed text.
    await repo.record_event(trace.id, {"event": "RunCompleted", "run_id": "run-stream-1"})

    detail = await get_trace(trace.id, tenant_a, session)
    assert detail["status"] == "completed"
    assert detail["output"].get("content") == "Hello world"
    root = next(span for span in detail["spans"] if span["kind"] == "run")
    assert root["output"].get("content") == "Hello world"
