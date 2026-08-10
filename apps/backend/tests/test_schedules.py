from datetime import UTC

import pytest

from app.db.repositories import TeamRepository
from app.scheduler.service import ScheduleRepository, SchedulerService, next_run_at


async def _published_team(session, context, suffix: str, member_ids: list):
    teams = TeamRepository(session, context)
    config = await teams.create_config(slug=f"scheduled-{suffix}", name=f"Scheduled {suffix}")
    version = await teams.create_draft(
        config_id=config.id,
        instructions="Return a short answer.",
        mode="coordinate",
        model_id="openai:gpt-4.1-mini",
        temperature=0,
        member_config_ids=member_ids,
    )
    await teams.publish(version.id)
    return config, version


def test_cron_and_timezone_validation():
    computed = next_run_at("*/5 * * * *", "America/New_York")
    assert computed.tzinfo is UTC
    with pytest.raises(ValueError, match="Invalid cron"):
        next_run_at("not cron", "UTC")
    with pytest.raises(ValueError, match="Unknown timezone"):
        next_run_at("0 9 * * *", "Mars/Olympus")


@pytest.mark.asyncio
async def test_schedule_isolation_enable_disable_and_execution_recording(
    session, tenant_a, tenant_b
):
    from app.db.repositories import AgentRepository

    session.info["tenant_id"] = tenant_a.tenant_id
    agents = AgentRepository(session, tenant_a)
    member = await agents.create_config(slug="sched-member", name="Sched Member")
    member_version = await agents.create_draft(
        config_id=member.id,
        instructions="Help",
        model_id="openai:gpt-4.1-mini",
        temperature=0,
    )
    await agents.publish(member_version.id)

    config, version = await _published_team(session, tenant_a, "a", [member.id])
    repo = ScheduleRepository(session, tenant_a)
    schedule = await repo.create(
        {
            "name": "Weekday digest",
            "cron_expression": "0 9 * * 1-5",
            "timezone": "UTC",
            "enabled": True,
            "target_type": "team",
            "target_id": config.id,
            "version_id": version.id,
            "message": "Summarize open support requests.",
            "input_payload": {"channel": "support"},
        }
    )
    assert schedule.next_run_at is not None

    disabled = await repo.update(schedule.id, {"enabled": False})
    assert disabled is not None
    assert disabled.enabled is False
    assert disabled.next_run_at is None
    enabled = await repo.update(schedule.id, {"enabled": True})
    assert enabled is not None
    assert enabled.next_run_at is not None

    async def mocked_runner(row, session_id):
        assert row.tenant_id == tenant_a.tenant_id
        assert session_id.startswith(f"schedule-{row.id}-")
        return {"content": "Digest complete", "run_id": "run-scheduled-1"}

    run = await SchedulerService(session, tenant_a, runner=mocked_runner).run(schedule.id)
    assert run.status == "completed"
    assert run.run_id == "run-scheduled-1"
    assert run.output["content"] == "Digest complete"
    assert schedule.last_status == "completed"
    assert schedule.last_run_at is not None
    assert len(await repo.runs(schedule.id)) == 1

    session.info["tenant_id"] = tenant_b.tenant_id
    other_repo = ScheduleRepository(session, tenant_b)
    assert await other_repo.list() == []
    assert await other_repo.get(schedule.id) is None
    with pytest.raises(LookupError, match="Schedule not found"):
        await SchedulerService(session, tenant_b, runner=mocked_runner).run(schedule.id)


@pytest.mark.asyncio
async def test_schedule_rejects_agent_target(session, tenant_a):
    from app.db.repositories import AgentRepository

    session.info["tenant_id"] = tenant_a.tenant_id
    agents = AgentRepository(session, tenant_a)
    config = await agents.create_config(slug="direct-agent", name="Direct Agent")
    version = await agents.create_draft(
        config_id=config.id,
        instructions="Help",
        model_id="openai:gpt-4.1-mini",
        temperature=0,
    )
    await agents.publish(version.id)
    repo = ScheduleRepository(session, tenant_a)
    with pytest.raises(ValueError, match="team or workflow"):
        await repo.create(
            {
                "name": "Bad agent schedule",
                "cron_expression": "0 9 * * 1-5",
                "timezone": "UTC",
                "enabled": True,
                "target_type": "agent",
                "target_id": config.id,
                "version_id": version.id,
                "message": "Should fail",
                "input_payload": {},
            }
        )
