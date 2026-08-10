from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from agno.scheduler.cron import compute_next_run, validate_cron_expr, validate_timezone
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.factory import (
    AgentFactoryService,
    RuntimeRequest,
    TeamFactoryService,
    TeamRuntimeRequest,
    WorkflowFactoryService,
    WorkflowRuntimeRequest,
)
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.db.models import (
    AgentStatus,
    AgentVersion,
    Role,
    Schedule,
    ScheduleRun,
    TeamVersion,
    Tenant,
    WorkflowVersion,
)
from app.db.session import SessionFactory
from app.tenancy.context import TenantContext

logger = get_logger(__name__)
TargetRunner = Callable[[Schedule, str], Awaitable[dict[str, Any]]]


def next_run_at(cron_expression: str, timezone: str, *, after: datetime | None = None) -> datetime:
    if not validate_cron_expr(cron_expression):
        raise ValueError("Invalid cron expression; use a standard 5-field cron")
    if not validate_timezone(timezone):
        raise ValueError("Unknown timezone")
    after_epoch = int(after.timestamp()) if after else None
    return datetime.fromtimestamp(
        compute_next_run(cron_expression, timezone, after_epoch=after_epoch),
        tz=UTC,
    )


class ScheduleRepository:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        session_tenant = session.info.get("tenant_id")
        if session_tenant is not None and session_tenant != context.tenant_id:
            raise RuntimeError("Database session tenant does not match request tenant")
        self.session = session
        self.context = context

    async def list(self) -> Sequence[Schedule]:
        rows = await self.session.scalars(
            select(Schedule)
            .where(Schedule.tenant_id == self.context.tenant_id)
            .order_by(Schedule.updated_at.desc())
        )
        return rows.all()

    async def get(self, schedule_id: uuid.UUID) -> Schedule | None:
        return await self.session.scalar(
            select(Schedule).where(
                Schedule.id == schedule_id,
                Schedule.tenant_id == self.context.tenant_id,
            )
        )

    async def create(self, values: dict[str, Any]) -> Schedule:
        await self._validate_target(
            values["target_type"], values["target_id"], values["version_id"]
        )
        target = self._target_columns(
            values.pop("target_type"), values.pop("target_id"), values.pop("version_id")
        )
        enabled = bool(values.get("enabled", True))
        cron_expression = str(values["cron_expression"])
        timezone = str(values.get("timezone", "UTC"))
        schedule = Schedule(
            id=uuid.uuid4(),
            tenant_id=self.context.tenant_id,
            created_by=self.context.user_id,
            next_run_at=next_run_at(cron_expression, timezone) if enabled else None,
            **values,
            **target,
        )
        self.session.add(schedule)
        await self.session.flush()
        return schedule

    async def update(self, schedule_id: uuid.UUID, values: dict[str, Any]) -> Schedule | None:
        schedule = await self.get(schedule_id)
        if schedule is None:
            return None
        target_type = str(values.pop("target_type", schedule.target_type))
        target_id = values.pop("target_id", schedule.target_id)
        version_id = values.pop("version_id", schedule.version_id)
        if (
            target_type != schedule.target_type
            or target_id != schedule.target_id
            or version_id != schedule.version_id
        ):
            await self._validate_target(target_type, target_id, version_id)
            for column in (
                "agent_config_id",
                "agent_version_id",
                "team_config_id",
                "team_version_id",
                "workflow_config_id",
                "workflow_version_id",
            ):
                setattr(schedule, column, None)
            for key, value in self._target_columns(target_type, target_id, version_id).items():
                setattr(schedule, key, value)
            schedule.target_type = target_type
        for key, value in values.items():
            setattr(schedule, key, value)
        if schedule.enabled:
            schedule.next_run_at = next_run_at(schedule.cron_expression, schedule.timezone)
        else:
            schedule.next_run_at = None
        await self.session.flush()
        return schedule

    async def delete(self, schedule_id: uuid.UUID) -> bool:
        result: Any = await self.session.execute(
            delete(Schedule).where(
                Schedule.id == schedule_id,
                Schedule.tenant_id == self.context.tenant_id,
            )
        )
        return bool(result.rowcount)

    async def runs(self, schedule_id: uuid.UUID, *, limit: int = 20) -> Sequence[ScheduleRun]:
        rows = await self.session.scalars(
            select(ScheduleRun)
            .where(
                ScheduleRun.tenant_id == self.context.tenant_id,
                ScheduleRun.schedule_id == schedule_id,
            )
            .order_by(ScheduleRun.started_at.desc())
            .limit(limit)
        )
        return rows.all()

    async def claim_due(self, now: datetime) -> Sequence[uuid.UUID]:
        due = await self.session.scalars(
            select(Schedule.id)
            .where(
                Schedule.tenant_id == self.context.tenant_id,
                Schedule.enabled.is_(True),
                Schedule.next_run_at <= now,
            )
            .order_by(Schedule.next_run_at)
            .limit(20)
        )
        claimed: list[uuid.UUID] = []
        for schedule_id in due:
            schedule = await self.get(schedule_id)
            if schedule is None or schedule.next_run_at is None:
                continue
            result: Any = await self.session.execute(
                update(Schedule)
                .where(
                    Schedule.id == schedule.id,
                    Schedule.tenant_id == self.context.tenant_id,
                    Schedule.enabled.is_(True),
                    Schedule.next_run_at == schedule.next_run_at,
                )
                .values(
                    next_run_at=next_run_at(
                        schedule.cron_expression,
                        schedule.timezone,
                        after=schedule.next_run_at,
                    ),
                    last_status="queued",
                    last_error=None,
                )
            )
            if result.rowcount:
                claimed.append(schedule.id)
        return claimed

    async def reclaim_stuck(self, now: datetime, *, older_than_seconds: int = 900) -> Sequence[uuid.UUID]:
        """Re-queue claims that never produced a run after a worker crash (M19)."""
        cutoff = now.timestamp() - older_than_seconds
        stuck = await self.session.scalars(
            select(Schedule.id)
            .where(
                Schedule.tenant_id == self.context.tenant_id,
                Schedule.enabled.is_(True),
                Schedule.last_status == "queued",
            )
            .limit(20)
        )
        claimed: list[uuid.UUID] = []
        for schedule_id in stuck:
            schedule = await self.get(schedule_id)
            if schedule is None:
                continue
            # Avoid reclaiming brand-new claims still being executed.
            ref = schedule.updated_at or schedule.last_run_at or schedule.created_at
            if ref is not None and ref.timestamp() > cutoff:
                continue
            claimed.append(schedule.id)
        return claimed

    async def _validate_target(
        self, target_type: str, target_id: uuid.UUID, version_id: uuid.UUID
    ) -> None:
        model: type[TeamVersion] | type[WorkflowVersion]
        config_column: Any
        if target_type == "team":
            model, config_column = TeamVersion, TeamVersion.team_config_id
        elif target_type == "workflow":
            model, config_column = WorkflowVersion, WorkflowVersion.workflow_config_id
        else:
            raise ValueError(
                "Target type must be team or workflow — agents are not directly schedulable"
            )
        found = await self.session.scalar(
            select(model.id).where(
                model.tenant_id == self.context.tenant_id,
                model.id == version_id,
                config_column == target_id,
                model.status == AgentStatus.published,
            )
        )
        if found is None:
            raise LookupError("Published target version not found for tenant")

    @staticmethod
    def _target_columns(
        target_type: str, target_id: uuid.UUID, version_id: uuid.UUID
    ) -> dict[str, Any]:
        return {
            "target_type": target_type,
            f"{target_type}_config_id": target_id,
            f"{target_type}_version_id": version_id,
        }


class SchedulerService:
    def __init__(
        self,
        session: AsyncSession,
        context: TenantContext,
        *,
        runner: TargetRunner | None = None,
    ) -> None:
        self.session = session
        self.context = context
        self.repo = ScheduleRepository(session, context)
        self.runner = runner or self._run_target

    async def run(self, schedule_id: uuid.UUID, *, trigger: str = "manual") -> ScheduleRun:
        schedule = await self.repo.get(schedule_id)
        if schedule is None:
            raise LookupError("Schedule not found")
        run = ScheduleRun(
            id=uuid.uuid4(),
            tenant_id=self.context.tenant_id,
            schedule_id=schedule.id,
            trigger=trigger,
            status="running",
            session_id=f"schedule-{schedule.id}-{uuid.uuid4()}",
            started_at=datetime.now(UTC),
        )
        schedule.last_status = "running"
        schedule.last_error = None
        self.session.add(run)
        await self.session.flush()
        try:
            timeout = get_settings().scheduler_run_timeout_seconds
            result = await asyncio.wait_for(
                self.runner(schedule, run.session_id),
                timeout=timeout,
            )
            run.output = result
            run.run_id = str(result.get("run_id")) if result.get("run_id") else None
            run.status = "completed"
            schedule.last_status = "completed"
        except TimeoutError:
            error = f"Scheduled run exceeded {get_settings().scheduler_run_timeout_seconds}s wall clock"
            run.status = "error"
            run.error = error
            schedule.last_status = "error"
            schedule.last_error = error
            logger.error(
                "scheduled_run_timeout",
                schedule_id=str(schedule.id),
                timeout_seconds=get_settings().scheduler_run_timeout_seconds,
            )
        except Exception as exc:
            error = str(exc)[:2000]
            run.status = "error"
            run.error = error
            schedule.last_status = "error"
            schedule.last_error = error
            logger.exception("scheduled_run_failed", schedule_id=str(schedule.id), error=error)
        completed_at = datetime.now(UTC)
        run.completed_at = completed_at
        schedule.last_run_at = completed_at
        if trigger == "manual" and schedule.enabled:
            schedule.next_run_at = next_run_at(schedule.cron_expression, schedule.timezone)
        await self.session.flush()
        return run

    async def _run_target(self, schedule: Schedule, session_id: str) -> dict[str, Any]:
        factory = AgentFactoryService(self.session, self.context)
        message = schedule.message
        if schedule.input_payload:
            message = (
                f"{message}\n\nStructured input:\n"
                f"{json.dumps(schedule.input_payload, sort_keys=True)}"
            )
        if schedule.target_type == "team":
            target = await TeamFactoryService(factory).create(
                TeamRuntimeRequest(
                    version_id=schedule.version_id,
                    session_id=session_id,
                    preview=False,
                )
            )
        elif schedule.target_type == "workflow":
            target = await WorkflowFactoryService(factory).create(
                WorkflowRuntimeRequest(
                    version_id=schedule.version_id,
                    session_id=session_id,
                    preview=False,
                )
            )
        else:
            raise ValueError(
                "Target type must be team or workflow — agents are not directly schedulable"
            )
        if hasattr(target, "arun"):
            output = await target.arun(message, stream=False)
        else:
            output = target.run(message, stream=False)
        if hasattr(output, "to_dict"):
            value = output.to_dict()
            if isinstance(value, dict):
                return value
        if hasattr(output, "model_dump"):
            value = output.model_dump(mode="json")
            if isinstance(value, dict):
                return value
        return {"content": str(output)}


class SchedulerWorker:
    """Tenant-by-tenant poller because Atlas tables use FORCE RLS.

    Agno 2.7.4's native schedule table and routes have no tenant key, and its
    executor only forwards an internal bearer token. Atlas therefore owns this
    small loop instead of enabling AgentOS ``scheduler=True``.

    Only the Redis leader instance runs ticks; claim_due CAS remains a second
    safety net against duplicate fires.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.poll_seconds = get_settings().scheduler_poll_seconds
        from app.scheduler.leader import SchedulerLeaderLock

        self._leader = SchedulerLeaderLock()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
        await self._leader.release()

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if await self._leader.try_acquire():
                    # Renew periodically during long ticks so TTL cannot expire mid-run.
                    renew_task = asyncio.create_task(self._renew_while_running())
                    try:
                        await self.tick()
                    finally:
                        renew_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await renew_task
                        await self._leader.renew()
            except Exception as exc:
                logger.exception("scheduler_tick_failed", error=str(exc))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    async def _renew_while_running(self) -> None:
        while True:
            await asyncio.sleep(max(5, self.poll_seconds // 2))
            if not await self._leader.renew():
                return

    async def tick(self) -> None:
        async with SessionFactory() as session:
            tenants = (
                await session.execute(
                    select(Tenant.id, Tenant.clerk_org_id).where(Tenant.is_active.is_(True))
                )
            ).all()
        for tenant_id, clerk_org_id in tenants:
            context = TenantContext(
                tenant_id=tenant_id,
                user_id="atlas-scheduler",
                role=Role.tenant_admin,
                clerk_org_id=clerk_org_id,
                principal_type="scheduler",
            )
            async with SessionFactory() as session, session.begin():
                if session.bind and session.bind.dialect.name == "postgresql":
                    await session.execute(
                        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                        {"tenant_id": str(tenant_id)},
                    )
                session.info["tenant_id"] = tenant_id
                claimed = list(
                    await ScheduleRepository(session, context).claim_due(datetime.now(UTC))
                )
                stuck = await ScheduleRepository(session, context).reclaim_stuck(
                    datetime.now(UTC),
                    older_than_seconds=get_settings().scheduler_run_timeout_seconds,
                )
                for schedule_id in stuck:
                    if schedule_id not in claimed:
                        claimed.append(schedule_id)
            for schedule_id in claimed:
                async with SessionFactory() as session, session.begin():
                    if session.bind and session.bind.dialect.name == "postgresql":
                        await session.execute(
                            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                            {"tenant_id": str(tenant_id)},
                        )
                    session.info["tenant_id"] = tenant_id
                    await SchedulerService(session, context).run(schedule_id, trigger="cron")
