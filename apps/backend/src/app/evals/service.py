from __future__ import annotations

import json
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
    TeamFactoryService,
    TeamRuntimeRequest,
    WorkflowFactoryService,
    WorkflowRuntimeRequest,
)
from app.db.models import (
    EvalCaseResult,
    EvalDefinition,
    EvalRun,
    TeamVersion,
    WorkflowVersion,
)
from app.tenancy.context import TenantContext
from app.tenancy.ids import validate_slug

CaseRunner = Callable[[EvalDefinition, str], Awaitable[dict[str, Any]]]

STRING_EVALUATORS = frozenset({"exact", "contains", "regex"})
AGNO_EVALUATORS = frozenset(
    {"accuracy", "agent_as_judge", "performance", "reliability"}
)
SUPPORTED_EVALUATORS = STRING_EVALUATORS | AGNO_EVALUATORS


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


def _parse_tool_names(expected: str) -> list[str]:
    text = expected.strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except json.JSONDecodeError:
            pass
    return [part.strip() for part in text.split(",") if part.strip()]


def _result_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "to_dict"):
        data = value.to_dict()
        return data if isinstance(data, dict) else {"value": data}
    if isinstance(value, dict):
        return value
    data: dict[str, Any] = {}
    for key in (
        "avg_score",
        "mean_score",
        "pass_rate",
        "avg_run_time",
        "eval_status",
        "failed_tool_calls",
        "passed_tool_calls",
        "missing_tool_calls",
        "additional_tool_calls",
    ):
        if hasattr(value, key):
            data[key] = getattr(value, key)
    return data


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
        model: type[TeamVersion] | type[WorkflowVersion]
        config_column: Any
        if target_type == "team":
            model, config_column = TeamVersion, TeamVersion.team_config_id
        elif target_type == "workflow":
            model, config_column = WorkflowVersion, WorkflowVersion.workflow_config_id
        else:
            raise ValueError(
                "Target type must be team or workflow — agents are not directly evaluable"
            )
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

    String graders (exact/contains/regex) stay local. Agno AccuracyEval,
    AgentAsJudgeEval, PerformanceEval, and ReliabilityEval grade factory-built
    team/workflow targets. Atlas persists tenant-keyed records because native
    AgentOS eval routes are not used in this shared-runtime deployment.
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
            details: dict[str, Any] = {}
            evaluator = str(case.get("evaluator") or "contains")
            if evaluator not in SUPPORTED_EVALUATORS:
                evaluator = "contains"
            try:
                if evaluator in AGNO_EVALUATORS and self.runner is self._run_target:
                    graded = await self._run_agno_case(definition, case, evaluator)
                else:
                    result = await self.runner(definition, str(case["input"]))
                    actual = str(result.get("content") or "")
                    metrics = result
                    score, passed = _score(
                        actual, str(case.get("expected_output") or ""), evaluator
                    )
                    details = {"mocked": bool(metrics.get("mocked", False))}
                    graded = {
                        "content": actual,
                        "score": score,
                        "passed": passed,
                        "details": details,
                        **{
                            key: metrics.get(key)
                            for key in (
                                "input_tokens",
                                "output_tokens",
                                "estimated_cost_usd",
                            )
                        },
                    }
                actual = str(graded.get("content") or "")
                details = dict(graded.get("details") or {})
                score = float(graded.get("score") or 0.0)
                passed = bool(graded.get("passed"))
                metrics = graded
            except Exception as exc:
                error = str(exc)[:2000]
                errors.append(error)
                score, passed = 0.0, False
            latency = int((time.perf_counter() - started) * 1000)
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
                    expected_output=str(case.get("expected_output") or ""),
                    actual_output=actual or None,
                    evaluator=evaluator,
                    score=score,
                    passed=passed,
                    latency_ms=latency,
                    input_tokens=case_input_tokens or None,
                    output_tokens=case_output_tokens or None,
                    estimated_cost_usd=case_cost or None,
                    error=error,
                    details=details,
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

    async def _run_agno_case(
        self, definition: EvalDefinition, case: dict[str, Any], evaluator: str
    ) -> dict[str, Any]:
        prompt = str(case["input"])
        expected = str(case.get("expected_output") or "")
        target = await self._build_target(definition)
        if evaluator == "accuracy":
            return await self._grade_accuracy(definition, target, prompt, expected)
        if evaluator == "agent_as_judge":
            return await self._grade_agent_as_judge(target, prompt, expected)
        if evaluator == "performance":
            return await self._grade_performance(target, prompt, expected)
        return await self._grade_reliability(target, prompt, expected)

    async def _grade_accuracy(
        self, definition: EvalDefinition, target: Any, prompt: str, expected: str
    ) -> dict[str, Any]:
        from agno.eval.accuracy import AccuracyEval

        kwargs: dict[str, Any] = {
            "input": prompt,
            "expected_output": expected,
            "name": f"atlas-{definition.slug}",
            "num_iterations": 1,
            "show_spinner": False,
            "print_summary": False,
            "print_results": False,
            "telemetry": False,
        }
        if definition.target_type == "team":
            kwargs["team"] = target
        else:
            # AccuracyEval only accepts agent|team; grade workflow output post-hoc.
            output = await self._invoke(target, prompt)
            content = _content(output)
            metrics = _metrics(output)
            accuracy = AccuracyEval(**kwargs)
            evaluation = await accuracy.aevaluate_answer(
                input=prompt,
                evaluator_agent=accuracy.get_evaluator_agent(),
                evaluation_input=(
                    f"Compare the actual answer to the expected answer.\n"
                    f"Input: {prompt}\nExpected: {expected}\nActual: {content}"
                ),
                evaluator_expected_output=expected,
                agent_output=content,
            )
            score = float(getattr(evaluation, "score", 0) or 0) / 10.0
            return {
                "content": content,
                "score": score,
                "passed": score >= 0.7,
                "details": {"evaluator": "accuracy", "result": _result_dict(evaluation)},
                **metrics,
            }

        accuracy = AccuracyEval(**kwargs)
        result = await accuracy.arun(print_summary=False, print_results=False)
        avg = float(getattr(result, "avg_score", None) or getattr(result, "mean_score", 0) or 0)
        score = avg / 10.0
        content = ""
        if result and getattr(result, "results", None):
            content = str(getattr(result.results[-1], "output", "") or "")
        return {
            "content": content,
            "score": score,
            "passed": score >= 0.7,
            "details": {"evaluator": "accuracy", "result": _result_dict(result)},
        }

    async def _grade_agent_as_judge(
        self, target: Any, prompt: str, expected: str
    ) -> dict[str, Any]:
        from agno.eval.agent_as_judge import AgentAsJudgeEval

        output = await self._invoke(target, prompt)
        content = _content(output)
        metrics = _metrics(output)
        judge = AgentAsJudgeEval(
            criteria=expected or "The answer is correct, helpful, and safe.",
            scoring_strategy="binary",
            show_spinner=False,
            print_summary=False,
            print_results=False,
            telemetry=False,
        )
        result = await judge.arun(
            input=prompt,
            output=content,
            print_summary=False,
            print_results=False,
        )
        passed = False
        score = 0.0
        if result is not None:
            if getattr(result, "results", None):
                passed = bool(result.results[0].passed)
                raw_score = result.results[0].score
                score = (
                    float(raw_score) / 10.0
                    if raw_score is not None
                    else (1.0 if passed else 0.0)
                )
            else:
                score = float(getattr(result, "pass_rate", 0.0) or 0.0)
                passed = score >= 1.0
        return {
            "content": content,
            "score": score,
            "passed": passed,
            "details": {"evaluator": "agent_as_judge", "result": _result_dict(result)},
            **metrics,
        }

    async def _grade_performance(
        self, target: Any, prompt: str, expected: str
    ) -> dict[str, Any]:
        from agno.eval.performance import PerformanceEval

        async def _func() -> Any:
            return await self._invoke(target, prompt)

        # Keep iterations low for product smoke runs; expected_output is max seconds.
        perf = PerformanceEval(
            func=_func,
            measure_runtime=True,
            measure_memory=False,
            warmup_runs=0,
            num_iterations=1,
            show_spinner=False,
            print_summary=False,
            print_results=False,
            telemetry=False,
        )
        result = await perf.arun(print_summary=False, print_results=False)
        avg = float(getattr(result, "avg_run_time", 0.0) or 0.0)
        try:
            limit = float(expected) if expected.strip() else None
        except ValueError:
            limit = None
        passed = True if limit is None else avg <= limit
        score = 1.0 if passed else 0.0
        return {
            "content": f"avg_run_time={avg:.4f}s",
            "score": score,
            "passed": passed,
            "details": {
                "evaluator": "performance",
                "max_seconds": limit,
                "result": _result_dict(result),
            },
        }

    async def _grade_reliability(
        self, target: Any, prompt: str, expected: str
    ) -> dict[str, Any]:
        from agno.eval.reliability import ReliabilityEval

        output = await self._invoke(target, prompt)
        content = _content(output)
        metrics = _metrics(output)
        expected_tools = _parse_tool_names(expected)
        kwargs: dict[str, Any] = {
            "expected_tool_calls": expected_tools or None,
            "show_spinner": False,
            "print_results": False,
            "telemetry": False,
        }
        # Team and workflow/agent run outputs expose messages for tool checks.
        if hasattr(output, "member_responses"):
            kwargs["team_response"] = output
        else:
            kwargs["agent_response"] = output
        reliability = ReliabilityEval(**kwargs)
        result = await reliability.arun(print_results=False)
        status = str(getattr(result, "eval_status", "") or "").lower()
        missing = list(getattr(result, "missing_tool_calls", None) or [])
        failed = list(getattr(result, "failed_tool_calls", None) or [])
        passed = status in {"pass", "passed", "success"} or (
            not missing and not failed and bool(expected_tools)
        )
        if not expected_tools:
            # No expected tools configured — treat as informational pass.
            passed = True
        return {
            "content": content,
            "score": 1.0 if passed else 0.0,
            "passed": passed,
            "details": {
                "evaluator": "reliability",
                "expected_tool_calls": expected_tools,
                "result": _result_dict(result),
            },
            **metrics,
        }

    async def _build_target(self, definition: EvalDefinition) -> Any:
        session_id = f"eval-{definition.id}-{uuid.uuid4()}"
        factory = AgentFactoryService(self.session, self.context)
        if definition.target_type == "team":
            return await TeamFactoryService(factory).create(
                TeamRuntimeRequest(
                    version_id=definition.version_id,
                    session_id=session_id,
                    preview=True,
                )
            )
        if definition.target_type == "workflow":
            return await WorkflowFactoryService(factory).create(
                WorkflowRuntimeRequest(
                    version_id=definition.version_id,
                    session_id=session_id,
                    preview=True,
                )
            )
        raise ValueError(
            "Target type must be team or workflow — agents are not directly evaluable"
        )

    async def _invoke(self, target: Any, prompt: str) -> Any:
        if hasattr(target, "arun"):
            return await target.arun(prompt, stream=False)
        return target.run(prompt, stream=False)

    async def _run_target(self, definition: EvalDefinition, prompt: str) -> dict[str, Any]:
        target = await self._build_target(definition)
        output = await self._invoke(target, prompt)
        return {"content": _content(output), **_metrics(output)}
