import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.db.models import EvalCaseResult, EvalDefinition, EvalRun, Role
from app.db.repositories import AgentRepository, TeamRepository, WorkflowRepository
from app.db.session import tenant_session
from app.evals.service import EvalRepository, EvalService
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/api/admin/evals", tags=["admin-evals"])
AdminContext = Annotated[
    TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
]
TenantSession = Annotated[AsyncSession, Depends(tenant_session)]


class EvalCaseIn(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    input: str = Field(min_length=1, max_length=100_000)
    expected_output: str = Field(min_length=1, max_length=100_000)
    evaluator: Literal["exact", "contains", "regex"] = "contains"


class EvalDefinitionCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)
    description: str | None = Field(default=None, max_length=4000)
    suite: str = Field(default="smoke", min_length=1, max_length=32)
    target_type: Literal["agent", "team", "workflow"]
    target_id: uuid.UUID
    version_id: uuid.UUID
    cases: list[EvalCaseIn] = Field(min_length=1, max_length=100)
    pass_threshold: float = Field(default=1.0, ge=0, le=1)
    active: bool = True
    run_on_publish: bool = False

    @model_validator(mode="after")
    def unique_case_keys(self) -> "EvalDefinitionCreateIn":
        keys = [case.key for case in self.cases]
        if len(keys) != len(set(keys)):
            raise ValueError("Eval case keys must be unique")
        return self


class EvalDefinitionUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(
        default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100
    )
    description: str | None = Field(default=None, max_length=4000)
    suite: str | None = Field(default=None, min_length=1, max_length=32)
    cases: list[EvalCaseIn] | None = Field(default=None, min_length=1, max_length=100)
    pass_threshold: float | None = Field(default=None, ge=0, le=1)
    active: bool | None = None
    run_on_publish: bool | None = None


def _run_out(run: EvalRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "eval_definition_id": run.eval_definition_id,
        "target_type": run.target_type,
        "target_id": run.target_id,
        "version_id": run.version_id,
        "trigger": run.trigger,
        "status": run.status,
        "score": run.score,
        "passed": run.passed,
        "total_cases": run.total_cases,
        "passed_cases": run.passed_cases,
        "latency_ms": run.latency_ms,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "estimated_cost_usd": run.estimated_cost_usd,
        "error": run.error,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
    }


def _case_out(case: EvalCaseResult) -> dict[str, Any]:
    return {
        "id": case.id,
        "case_key": case.case_key,
        "name": case.name,
        "input": case.input,
        "expected_output": case.expected_output,
        "actual_output": case.actual_output,
        "evaluator": case.evaluator,
        "score": case.score,
        "passed": case.passed,
        "latency_ms": case.latency_ms,
        "input_tokens": case.input_tokens,
        "output_tokens": case.output_tokens,
        "estimated_cost_usd": case.estimated_cost_usd,
        "error": case.error,
        "details": case.details,
    }


async def _definition_out(
    definition: EvalDefinition, repo: EvalRepository
) -> dict[str, Any]:
    runs = await repo.list_runs(definition.id, limit=20)
    latest = runs[0] if runs else None
    return {
        "id": definition.id,
        "name": definition.name,
        "slug": definition.slug,
        "description": definition.description,
        "suite": definition.suite,
        "target_type": definition.target_type,
        "target_id": definition.target_id,
        "version_id": definition.version_id,
        "cases": definition.cases,
        "pass_threshold": definition.pass_threshold,
        "active": definition.active,
        "run_on_publish": definition.run_on_publish,
        "created_by": definition.created_by,
        "created_at": definition.created_at,
        "updated_at": definition.updated_at,
        "latest_run": _run_out(latest) if latest else None,
        "runs": [_run_out(run) for run in runs],
    }


@router.get("")
async def list_evals(context: AdminContext, session: TenantSession) -> list[dict[str, Any]]:
    repo = EvalRepository(session, context)
    return [
        await _definition_out(definition, repo)
        for definition in await repo.list_definitions()
    ]


@router.get("/targets/catalog")
async def list_eval_targets(
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
            draft = await repo.get_latest_draft(config.id)
            version_id = draft.id if draft else config.published_version_id
            if version_id:
                output.append(
                    {
                        "target_type": target_type,
                        "target_id": config.id,
                        "version_id": version_id,
                        "name": config.name,
                        "slug": config.slug,
                        "version_status": "draft" if draft else "published",
                    }
                )
    return output


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_eval(
    body: EvalDefinitionCreateIn, context: AdminContext, session: TenantSession
) -> dict[str, Any]:
    repo = EvalRepository(session, context)
    try:
        definition = await repo.create_definition(body.model_dump(mode="python"))
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _definition_out(definition, repo)


@router.get("/{definition_id}")
async def get_eval(
    definition_id: uuid.UUID, context: AdminContext, session: TenantSession
) -> dict[str, Any]:
    repo = EvalRepository(session, context)
    definition = await repo.get_definition(definition_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="Eval definition not found")
    output = await _definition_out(definition, repo)
    output["runs"] = [
        {
            **_run_out(run),
            "case_results": [_case_out(case) for case in await repo.case_results(run.id)],
        }
        for run in await repo.list_runs(definition.id)
    ]
    return output


@router.patch("/{definition_id}")
async def update_eval(
    definition_id: uuid.UUID,
    body: EvalDefinitionUpdateIn,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    repo = EvalRepository(session, context)
    definition = await repo.update_definition(
        definition_id, body.model_dump(exclude_none=True, mode="python")
    )
    if definition is None:
        raise HTTPException(status_code=404, detail="Eval definition not found")
    return await _definition_out(definition, repo)


@router.delete("/{definition_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_eval(
    definition_id: uuid.UUID, context: AdminContext, session: TenantSession
) -> Response:
    if not await EvalRepository(session, context).delete_definition(definition_id):
        raise HTTPException(status_code=404, detail="Eval definition not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{definition_id}/runs", status_code=status.HTTP_201_CREATED)
async def run_eval(
    definition_id: uuid.UUID, context: AdminContext, session: TenantSession
) -> dict[str, Any]:
    service = EvalService(session, context)
    try:
        run = await service.run(definition_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        **_run_out(run),
        "case_results": [_case_out(case) for case in await service.repo.case_results(run.id)],
    }


@router.get("/runs/{run_id}")
async def get_eval_run(
    run_id: uuid.UUID, context: AdminContext, session: TenantSession
) -> dict[str, Any]:
    repo = EvalRepository(session, context)
    run = await repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Eval run not found")
    return {
        **_run_out(run),
        "case_results": [_case_out(case) for case in await repo.case_results(run.id)],
    }
