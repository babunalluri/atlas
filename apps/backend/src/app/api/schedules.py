import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.db.models import Role, Schedule, ScheduleRun
from app.db.repositories import AgentRepository, TeamRepository, WorkflowRepository
from app.db.session import tenant_session
from app.scheduler.service import ScheduleRepository, SchedulerService
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/api/admin/schedules", tags=["admin-schedules"])
AdminContext = Annotated[
    TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
]
TenantSession = Annotated[AsyncSession, Depends(tenant_session)]


class ScheduleCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    cron_expression: str = Field(min_length=1, max_length=255)
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    enabled: bool = True
    target_type: Literal["agent", "team", "workflow"]
    target_id: uuid.UUID
    version_id: uuid.UUID
    message: str = Field(min_length=1, max_length=100_000)
    input_payload: dict[str, Any] = Field(default_factory=dict)


class ScheduleUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    cron_expression: str | None = Field(default=None, min_length=1, max_length=255)
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    target_type: Literal["agent", "team", "workflow"] | None = None
    target_id: uuid.UUID | None = None
    version_id: uuid.UUID | None = None
    message: str | None = Field(default=None, min_length=1, max_length=100_000)
    input_payload: dict[str, Any] | None = None


class ScheduleStateIn(BaseModel):
    enabled: bool


def _run_out(run: ScheduleRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "schedule_id": run.schedule_id,
        "trigger": run.trigger,
        "status": run.status,
        "session_id": run.session_id,
        "run_id": run.run_id,
        "output": run.output,
        "error": run.error,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
    }


async def _schedule_out(schedule: Schedule, repo: ScheduleRepository) -> dict[str, Any]:
    runs = await repo.runs(schedule.id, limit=10)
    return {
        "id": schedule.id,
        "name": schedule.name,
        "cron_expression": schedule.cron_expression,
        "timezone": schedule.timezone,
        "enabled": schedule.enabled,
        "target_type": schedule.target_type,
        "target_id": schedule.target_id,
        "version_id": schedule.version_id,
        "message": schedule.message,
        "input_payload": schedule.input_payload,
        "created_by": schedule.created_by,
        "last_run_at": schedule.last_run_at,
        "next_run_at": schedule.next_run_at,
        "last_status": schedule.last_status,
        "last_error": schedule.last_error,
        "created_at": schedule.created_at,
        "updated_at": schedule.updated_at,
        "runs": [_run_out(run) for run in runs],
    }


@router.get("")
async def list_schedules(
    context: AdminContext, session: TenantSession
) -> list[dict[str, Any]]:
    repo = ScheduleRepository(session, context)
    return [await _schedule_out(schedule, repo) for schedule in await repo.list()]


@router.get("/targets/catalog")
async def list_schedule_targets(
    context: AdminContext, session: TenantSession
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    repositories: list[tuple[str, Any]] = [
        ("agent", AgentRepository(session, context)),
        ("team", TeamRepository(session, context)),
        ("workflow", WorkflowRepository(session, context)),
    ]
    for target_type, repo in repositories:
        for config in await repo.list_configs():
            if config.published_version_id:
                output.append(
                    {
                        "target_type": target_type,
                        "target_id": config.id,
                        "version_id": config.published_version_id,
                        "name": config.name,
                        "slug": config.slug,
                    }
                )
    return output


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_schedule(
    body: ScheduleCreateIn, context: AdminContext, session: TenantSession
) -> dict[str, Any]:
    repo = ScheduleRepository(session, context)
    try:
        schedule = await repo.create(body.model_dump(mode="python"))
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Schedule name already exists") from exc
    return await _schedule_out(schedule, repo)


@router.get("/{schedule_id}")
async def get_schedule(
    schedule_id: uuid.UUID, context: AdminContext, session: TenantSession
) -> dict[str, Any]:
    repo = ScheduleRepository(session, context)
    schedule = await repo.get(schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return await _schedule_out(schedule, repo)


@router.patch("/{schedule_id}")
async def update_schedule(
    schedule_id: uuid.UUID,
    body: ScheduleUpdateIn,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    repo = ScheduleRepository(session, context)
    try:
        schedule = await repo.update(
            schedule_id, body.model_dump(exclude_none=True, mode="python")
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Schedule name already exists") from exc
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return await _schedule_out(schedule, repo)


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: uuid.UUID, context: AdminContext, session: TenantSession
) -> Response:
    if not await ScheduleRepository(session, context).delete(schedule_id):
        raise HTTPException(status_code=404, detail="Schedule not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{schedule_id}/state")
async def set_schedule_state(
    schedule_id: uuid.UUID,
    body: ScheduleStateIn,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    repo = ScheduleRepository(session, context)
    schedule = await repo.update(schedule_id, {"enabled": body.enabled})
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return await _schedule_out(schedule, repo)


@router.post("/{schedule_id}/run", status_code=status.HTTP_201_CREATED)
async def run_schedule_now(
    schedule_id: uuid.UUID, context: AdminContext, session: TenantSession
) -> dict[str, Any]:
    try:
        run = await SchedulerService(session, context).run(schedule_id, trigger="manual")
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _run_out(run)
