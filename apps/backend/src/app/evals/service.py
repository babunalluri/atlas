from __future__ import annotations

import re
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.factory import (
    AgentFactoryService,
    RuntimeRequest,
    TeamFactoryService,
    TeamRuntimeRequest,
    WorkflowFactoryService,
    WorkflowRuntimeRequest,
)
from app.db.models import (
    AgentVersion,
    EvalCaseResult,
    EvalDefinition,
    EvalRun,
    TeamVersion,
    WorkflowVersion,
)
from app.tenancy.context import TenantContext
from app.tenancy.ids import validate_slug

CaseRunner = Callable[[EvalDefinition, str], Awaitable[dict[str, Any]]]


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())


def _score(actual: str, expected: str, evaluator: str) -> tuple[float, bool]:
    actual_value = _normalized(actual)
    expected_value = _normalized(expected)
    if evaluator == "exact":
        passed = actual_value == expected_value
    elif evaluator == "regex":
        try:
            passed = re.search(expected, actual, re.IGNORECASE) is not None
        except re.error:
            passed = False
    else:
        passed = bool(expected_value) and expected_value in actual_value
    return (1.0 if passed else 0.0), passed


def _content(output: Any) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, dict):
        return str(output.get("content") or output.get("response") or output)
    for attribute in ("content", "response", "message"):
        value = getattr(output, attribute, None)
        if value is not None:
            return str(value)
    return str(output)


def _metrics(output: Any) -> dict[str, Any]:
    raw = getattr(output, "metrics", None)
    if raw is not None and hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    if not isinstance(raw, dict) and hasattr(output, "to_dict"):
        value = output.to_dict()
        raw = value.get("metrics") if isinstance(value, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    return {
        "input_tokens": raw.get("input_tokens") or raw.get("prompt_tokens"),
        "output_tokens": raw.get("output_tokens") or raw.get("completion_tokens"),
        "estimated_cost_usd": raw.get("cost") or raw.get("estimated_cost"),
    }


class EvalRepository:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        session_tenant = session.info.get("tenant_id")
        if session_tenant is not None and session_tenant != context.tenant_id:
            raise RuntimeError("Database session tenant does not match request tenant")
        self.session = session
        self.context = context

    async def list_definitions(self) -> Sequence[EvalDefinition]:
        rows = await self.session.scalars(
            select(EvalDefinition)
            .where(EvalDefinition.tenant_id == self.context.tenant_id)
            .order_by(EvalDefinition.updated_at.desc())
        )
        return rows.all()

    async def get_definition(self, definition_id: uuid.UUID) -> EvalDefinition | None:
        return await self.session.scalar(
            select(EvalDefinition).where(
                EvalDefinition.id == definition_id,
                EvalDefinition.tenant_id == self.context.tenant_id,
            )
        )

    async def create_definition(self, values: dict[str, Any]) -> EvalDefinition:
        await self._validate_target(
            values["target_type"], values["target_id"], values["version_id"]
        )
        target = self._target_columns(
            values["target_type"], values.pop("target_id"), values.pop("version_id")
        )
        definition = EvalDefinition(
            id=uuid.uuid4(),
            tenant_id=self.context.tenant_id,
            slug=validate_slug(values.pop("slug")),
            created_by=self.context.user_id,
            **values,
            **target,
        )
        self.session.add(definition)
        await self.session.flush()
        return definition

    async def update_definition(
        self, definition_id: uuid.UUID, values: dict[str, Any]
    ) -> EvalDefinition | None:
        definition = await self.get_definition(definition_id)
        if definition is None:
            return None
        for key, value in values.items():
            if key == "slug":
                value = validate_slug(value)
            setattr(definition, key, value)
        await self.session.flush()
        return definition

    async def delete_definition(self, definition_id: uuid.UUID) -> bool:
        result: Any = await self.session.execute(
            delete(EvalDefinition).where(
                EvalDefinition.id == definition_id,
                EvalDefinition.tenant_id == self.context.tenant_id,
            )
        )
        return bool(result.rowcount)

    async def list_runs(
        self, definition_id: uuid.UUID | None = None, *, limit: int = 100
    ) -> Sequence[EvalRun]:
        statement = select(EvalRun).where(EvalRun.tenant_id == self.context.tenant_id)
        if definition_id:
            statement = statement.where(EvalRun.eval_definition_id == definition_id)
        rows = await self.session.scalars(
            statement.order_by(EvalRun.started_at.desc()).limit(limit)
        )
        return rows.all()

    async def get_run(self, run_id: uuid.UUID) -> EvalRun | None:
        return await self.session.scalar(
            select(EvalRun).where(
                EvalRun.id == run_id,
                EvalRun.tenant_id == self.context.tenant_id,
            )
        )

    async def case_results(self, run_id: uuid.UUID) -> Sequence[EvalCaseResult]:
        rows = await self.session.scalars(
            select(EvalCaseResult)
            .where(
                EvalCaseResult.eval_run_id == run_id,
                EvalCaseResult.tenant_id == self.context.tenant_id,
            )
            .order_by(EvalCaseResult.created_at, EvalCaseResult.case_key)
        )
        return rows.all()

    async def latest_for_version(self, target_type: str, version_id: uuid.UUID) -> EvalRun | None:
        return await self.session.scalar(
            select(EvalRun)
            .where(
                EvalRun.tenant_id == self.context.tenant_id,
                EvalRun.target_type == target_type,
                EvalRun.version_id == version_id,
                EvalRun.status == "completed",
            )
            .order_by(EvalRun.completed_at.desc())
            .limit(1)
        )

    async def _validate_target(
        self, target_type: str, target_id: uuid.UUID, version_id: uuid.UUID
    ) -> None:
        model: type[AgentVersion] | type[TeamVersion] | type[WorkflowVersion]
        config_column: Any
        if target_type == "agent":
            model, config_column = AgentVersion, AgentVersion.agent_config_id
        elif target_type == "team":
            model, config_column = TeamVersion, TeamVersion.team_config_id
        elif target_type == "workflow":
            model, config_column = WorkflowVersion, WorkflowVersion.workflow_config_id
        else:
            raise ValueError("Target type must be agent, team, or workflow")
        found = await self.session.scalar(
            select(model.id).where(
                model.tenant_id == self.context.tenant_id,
                model.id == version_id,
                config_column == target_id,
            )
        )
        if found is None:
            raise LookupError("Target version not found for tenant")

    @staticmethod
    def _target_columns(
        target_type: str, target_id: uuid.UUID, version_id: uuid.UUID
    ) -> dict[str, uuid.UUID]:
        return {
            f"{target_type}_config_id": target_id,
            f"{target_type}_version_id": version_id,
        }


class EvalService:
    """Product-owned eval runner.

    Agno 2.7.4's AccuracyEval and PerformanceEval inform the case/result shape,
    while Atlas persists its own tenant-keyed records because native AgentOS
    eval routes are not tenant-aware enough for this shared-runtime deployment.
    """

    def __init__(
        self,
        session: AsyncSession,
        context: TenantContext,
        *,
        runner: CaseRunner | None = None,
    ) -> None:
        self.session = session
        self.context = context
        self.repo = EvalRepository(session, context)
        self.runner = runner or self._run_target

    async def run(self, definition_id: uuid.UUID, *, trigger: str = "manual") -> EvalRun:
        definition = await self.repo.get_definition(definition_id)
        if definition is None:
            raise LookupError("Eval definition not found")
        if not definition.active:
            raise ValueError("Eval definition is inactive")
        run = EvalRun(
            id=uuid.uuid4(),
            tenant_id=self.context.tenant_id,
            eval_definition_id=definition.id,
            target_type=definition.target_type,
            target_id=definition.target_id,
            version_id=definition.version_id,
            trigger=trigger,
            status="running",
            total_cases=len(definition.cases),
            started_at=datetime.now(UTC),
        )
        self.session.add(run)
        await self.session.flush()

        total_latency = 0
        input_tokens = 0
        output_tokens = 0
        cost = 0.0
        errors: list[str] = []
        for index, case in enumerate(definition.cases):
            started = time.perf_counter()
            error: str | None = None
            actual = ""
            metrics: dict[str, Any] = {}
            try:
                result = await self.runner(definition, str(case["input"]))
                actual = str(result.get("content") or "")
                metrics = result
            except Exception as exc:
                error = str(exc)[:2000]
                errors.append(error)
            latency = int((time.perf_counter() - started) * 1000)
            evaluator = str(case.get("evaluator") or "contains")
            score, passed = _score(actual, str(case["expected_output"]), evaluator)
            if error:
                score, passed = 0.0, False
            case_input_tokens = int(metrics.get("input_tokens") or 0)
            case_output_tokens = int(metrics.get("output_tokens") or 0)
            case_cost = float(metrics.get("estimated_cost_usd") or 0)
            total_latency += latency
            input_tokens += case_input_tokens
            output_tokens += case_output_tokens
            cost += case_cost
            run.passed_cases += int(passed)
            self.session.add(
                EvalCaseResult(
                    id=uuid.uuid4(),
                    tenant_id=self.context.tenant_id,
                    eval_run_id=run.id,
                    case_key=str(case.get("key") or f"case-{index + 1}"),
                    name=str(case.get("name") or f"Case {index + 1}"),
                    input=str(case["input"]),
                    expected_output=str(case["expected_output"]),
                    actual_output=actual or None,
                    evaluator=evaluator,
                    score=score,
                    passed=passed,
                    latency_ms=latency,
                    input_tokens=case_input_tokens or None,
                    output_tokens=case_output_tokens or None,
                    estimated_cost_usd=case_cost or None,
                    error=error,
                    details={"mocked": bool(metrics.get("mocked", False))},
                )
            )

        run.score = run.passed_cases / run.total_cases if run.total_cases else 0.0
        run.passed = bool(run.total_cases) and run.score >= definition.pass_threshold
        run.status = "completed"
        run.latency_ms = total_latency
        run.input_tokens = input_tokens or None
        run.output_tokens = output_tokens or None
        run.estimated_cost_usd = cost or None
        run.error = "; ".join(errors)[:2000] if errors else None
        run.completed_at = datetime.now(UTC)
        await self.session.flush()
        return run

    async def _run_target(self, definition: EvalDefinition, prompt: str) -> dict[str, Any]:
        session_id = f"eval-{definition.id}-{uuid.uuid4()}"
        factory = AgentFactoryService(self.session, self.context)
        if definition.target_type == "agent":
            target = await factory.create(
                RuntimeRequest(
                    version_id=definition.version_id,
                    session_id=session_id,
                    preview=True,
                )
            )
        elif definition.target_type == "team":
            target = await TeamFactoryService(factory).create(
                TeamRuntimeRequest(
                    version_id=definition.version_id,
                    session_id=session_id,
                    preview=True,
                )
            )
        else:
            target = await WorkflowFactoryService(factory).create(
                WorkflowRuntimeRequest(
                    version_id=definition.version_id,
                    session_id=session_id,
                    preview=True,
                )
            )
        if hasattr(target, "arun"):
            output = await target.arun(prompt, stream=False)
        else:
            output = target.run(prompt, stream=False)
        return {"content": _content(output), **_metrics(output)}
