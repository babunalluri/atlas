import pytest

from app.db.repositories import AgentRepository, SessionRepository
from app.evals.service import EvalRepository, EvalService
from app.metrics.service import MetricsService
from app.observability.repository import TraceRepository


async def _team_and_conversation(session, context, suffix: str):
    from app.db.repositories import TeamRepository

    agents = AgentRepository(session, context)
    member = await agents.create_config(slug=f"quality-member-{suffix}", name=f"Member {suffix}")
    member_version = await agents.create_draft(
        config_id=member.id,
        instructions="Return concise, correct answers.",
        model_id="openai:gpt-4.1-mini",
        temperature=0,
    )
    await agents.publish(member_version.id)

    teams = TeamRepository(session, context)
    config = await teams.create_config(slug=f"quality-{suffix}", name=f"Quality {suffix}")
    version = await teams.create_draft(
        config_id=config.id,
        instructions="Coordinate carefully.",
        mode="coordinate",
        model_id="openai:gpt-4.1-mini",
        temperature=0,
        member_config_ids=[member.id],
    )
    await teams.publish(version.id)
    conversation = await SessionRepository(session, context).pin_team(
        external_session_id=f"quality-session-{suffix}",
        team_config_id=config.id,
        team_version_id=version.id,
        runtime_session_id=f"runtime-{suffix}",
        runtime_user_id=f"user-{suffix}",
    )
    return config, version, conversation


@pytest.mark.asyncio
async def test_eval_crud_run_results_and_tenant_isolation(session, tenant_a, tenant_b):
    session.info["tenant_id"] = tenant_a.tenant_id
    config, version, _ = await _team_and_conversation(session, tenant_a, "a")
    repo = EvalRepository(session, tenant_a)
    definition = await repo.create_definition(
        {
            "name": "Smoke correctness",
            "slug": "smoke-correctness",
            "description": "Deterministic mocked-model test",
            "suite": "smoke",
            "target_type": "team",
            "target_id": config.id,
            "version_id": version.id,
            "cases": [
                {
                    "key": "capital",
                    "name": "Knows the capital",
                    "input": "What is the capital of France?",
                    "expected_output": "Paris",
                    "evaluator": "contains",
                }
            ],
            "pass_threshold": 1,
            "active": True,
            "run_on_publish": True,
        }
    )
    updated = await repo.update_definition(definition.id, {"description": "Updated"})
    assert updated is not None and updated.description == "Updated"

    async def mocked_runner(_definition, prompt):
        assert prompt == "What is the capital of France?"
        return {
            "content": "Paris is the capital of France.",
            "input_tokens": 7,
            "output_tokens": 8,
            "estimated_cost_usd": 0.001,
            "mocked": True,
        }

    run = await EvalService(session, tenant_a, runner=mocked_runner).run(definition.id)
    assert run.passed is True
    assert run.score == 1
    assert run.input_tokens == 7
    results = await repo.case_results(run.id)
    assert len(results) == 1
    assert results[0].passed is True
    assert results[0].details == {"mocked": True}

    exact = await repo.create_definition(
        {
            "name": "Exact match",
            "slug": "exact-match",
            "description": None,
            "suite": "smoke",
            "target_type": "team",
            "target_id": config.id,
            "version_id": version.id,
            "cases": [
                {
                    "key": "exact",
                    "name": "Exact",
                    "input": "Say Paris",
                    "expected_output": "Paris",
                    "evaluator": "exact",
                }
            ],
            "pass_threshold": 1,
            "active": True,
            "run_on_publish": False,
        }
    )

    async def exact_runner(_definition, prompt):
        return {"content": "Paris", "mocked": True}

    exact_run = await EvalService(session, tenant_a, runner=exact_runner).run(exact.id)
    assert exact_run.passed is True

    session.info["tenant_id"] = tenant_b.tenant_id
    other_repo = EvalRepository(session, tenant_b)
    assert await other_repo.list_definitions() == []
    assert await other_repo.get_definition(definition.id) is None
    assert await other_repo.get_run(run.id) is None

    session.info["tenant_id"] = tenant_a.tenant_id
    assert await repo.delete_definition(definition.id) is True
    assert await repo.get_definition(definition.id) is None
    assert await repo.delete_definition(exact.id) is True


@pytest.mark.asyncio
async def test_metric_aggregates_are_durable_and_tenant_isolated(session, tenant_a, tenant_b):
    session.info["tenant_id"] = tenant_a.tenant_id
    config, version, conversation = await _team_and_conversation(session, tenant_a, "metrics-a")
    traces = TraceRepository(session, tenant_a)
    trace = await traces.start(
        conversation=conversation,
        target_id=config.id,
        version_id=version.id,
        name="Measured run",
        message="hello",
    )
    await traces.record_event(
        trace.id,
        {
            "event": "RunCompleted",
            "run_id": "run-metrics-a",
            "content": "done",
            "metrics": {
                "input_tokens": 10,
                "output_tokens": 5,
                "estimated_cost": 0.002,
            },
        },
    )
    dashboard = await MetricsService(session, tenant_a).dashboard(days=30)
    assert dashboard["kpis"]["runs"] == 1
    assert dashboard["kpis"]["success_rate"] == 1
    assert dashboard["kpis"]["input_tokens"] == 10
    assert dashboard["top_targets"][0]["target_id"] == config.id

    session.info["tenant_id"] = tenant_b.tenant_id
    other = await MetricsService(session, tenant_b).dashboard(days=30)
    assert other["kpis"]["runs"] == 0
    assert other["top_targets"] == []

    session.info["tenant_id"] = tenant_a.tenant_id
    rerun = await MetricsService(session, tenant_a).dashboard(days=30)
    assert rerun["kpis"]["runs"] == 1
    assert len(rerun["daily"]) == 1
