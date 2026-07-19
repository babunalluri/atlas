from __future__ import annotations

import math
import uuid
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AgentConfig,
    ApprovalBinding,
    ConversationSession,
    MetricDailyAggregate,
    TeamConfig,
    TraceRecord,
    TraceSpan,
    WorkflowConfig,
)
from app.tenancy.context import TenantContext


def _day(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _run_metrics(trace: TraceRecord) -> tuple[int, int, float]:
    metrics = trace.output.get("metrics") if isinstance(trace.output, dict) else {}
    metrics = metrics if isinstance(metrics, dict) else {}
    return (
        int(metrics.get("input_tokens") or metrics.get("prompt_tokens") or 0),
        int(metrics.get("output_tokens") or metrics.get("completion_tokens") or 0),
        float(metrics.get("cost") or metrics.get("estimated_cost") or 0),
    )


class MetricsService:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        session_tenant = session.info.get("tenant_id")
        if session_tenant is not None and session_tenant != context.tenant_id:
            raise RuntimeError("Database session tenant does not match request tenant")
        self.session = session
        self.context = context

    async def refresh(self, *, days: int = 30) -> Sequence[MetricDailyAggregate]:
        end = _day(datetime.now(UTC)) + timedelta(days=1)
        start = end - timedelta(days=days)
        traces = list(
            (
                await self.session.scalars(
                    select(TraceRecord).where(
                        TraceRecord.tenant_id == self.context.tenant_id,
                        TraceRecord.started_at >= start,
                        TraceRecord.started_at < end,
                    )
                )
            ).all()
        )
        trace_ids = [trace.id for trace in traces]
        spans: list[TraceSpan] = []
        if trace_ids:
            spans = list(
                (
                    await self.session.scalars(
                        select(TraceSpan).where(
                            TraceSpan.tenant_id == self.context.tenant_id,
                            TraceSpan.trace_id.in_(trace_ids),
                            TraceSpan.kind == "tool",
                        )
                    )
                ).all()
            )
        approvals = list(
            (
                await self.session.execute(
                    select(
                        ApprovalBinding.created_at,
                        ConversationSession.target_type,
                        ConversationSession.agent_config_id,
                        ConversationSession.team_config_id,
                        ConversationSession.workflow_config_id,
                    )
                    .join(
                        ConversationSession,
                        (ConversationSession.id == ApprovalBinding.session_id)
                        & (ConversationSession.tenant_id == ApprovalBinding.tenant_id),
                    )
                    .where(
                        ApprovalBinding.tenant_id == self.context.tenant_id,
                        ApprovalBinding.created_at >= start,
                        ApprovalBinding.created_at < end,
                    )
                )
            ).all()
        )

        tool_counts: dict[tuple[datetime, uuid.UUID], Counter[str]] = defaultdict(Counter)
        all_tool_counts: dict[datetime, Counter[str]] = defaultdict(Counter)
        trace_by_id = {trace.id: trace for trace in traces}
        for span in spans:
            trace = trace_by_id.get(span.trace_id)
            if trace is None:
                continue
            attributes = span.attributes if isinstance(span.attributes, dict) else {}
            tool_name = str(
                attributes.get("tool_name")
                or attributes.get("name")
                or attributes.get("tool")
                or span.name
            )
            date = _day(trace.started_at)
            tool_counts[(date, trace.target_id)][tool_name] += 1
            all_tool_counts[date][tool_name] += 1

        approval_counts: Counter[tuple[datetime, str, uuid.UUID]] = Counter()
        all_approval_counts: Counter[datetime] = Counter()
        for created_at, target_type, agent_id, team_id, workflow_id in approvals:
            target_id = agent_id or team_id or workflow_id
            if target_id is None:
                continue
            date = _day(created_at)
            approval_counts[(date, target_type, target_id)] += 1
            all_approval_counts[date] += 1

        grouped: dict[tuple[datetime, str, uuid.UUID], list[TraceRecord]] = defaultdict(list)
        by_day: dict[datetime, list[TraceRecord]] = defaultdict(list)
        for trace in traces:
            date = _day(trace.started_at)
            grouped[(date, trace.target_type, trace.target_id)].append(trace)
            by_day[date].append(trace)

        await self.session.execute(
            delete(MetricDailyAggregate).where(
                MetricDailyAggregate.tenant_id == self.context.tenant_id,
                MetricDailyAggregate.metric_date >= start,
                MetricDailyAggregate.metric_date < end,
            )
        )
        rows: list[MetricDailyAggregate] = []
        for (date, target_type, target_id), items in grouped.items():
            aggregate = self._aggregate(
                date,
                target_type,
                target_id,
                items,
                tool_counts[(date, target_id)],
                approval_counts[(date, target_type, target_id)],
            )
            rows.append(aggregate)
            self.session.add(aggregate)
        for date, items in by_day.items():
            aggregate = self._aggregate(
                date,
                "all",
                None,
                items,
                all_tool_counts[date],
                all_approval_counts[date],
            )
            rows.append(aggregate)
            self.session.add(aggregate)
        await self.session.flush()
        return rows

    def _aggregate(
        self,
        date: datetime,
        target_type: str,
        target_id: uuid.UUID | None,
        traces: list[TraceRecord],
        tools: Counter[str],
        approval_waits: int,
    ) -> MetricDailyAggregate:
        token_metrics = [_run_metrics(trace) for trace in traces]
        durations = [trace.duration_ms for trace in traces if trace.duration_ms is not None]
        return MetricDailyAggregate(
            id=uuid.uuid4(),
            tenant_id=self.context.tenant_id,
            metric_date=date,
            target_type=target_type,
            target_id=target_id,
            run_count=len(traces),
            success_count=sum(trace.status == "completed" for trace in traces),
            error_count=sum(trace.status in {"error", "cancelled"} for trace in traces),
            paused_count=sum(trace.status == "paused" for trace in traces),
            latency_p50_ms=_percentile(durations, 0.5),
            latency_p95_ms=_percentile(durations, 0.95),
            input_tokens=sum(item[0] for item in token_metrics),
            output_tokens=sum(item[1] for item in token_metrics),
            estimated_cost_usd=sum(item[2] for item in token_metrics),
            tool_calls=sum(tools.values()),
            approval_waits=approval_waits,
            unique_sessions=len({trace.session_id for trace in traces}),
            top_tools=[
                {"name": name, "count": count} for name, count in tools.most_common(10)
            ],
        )

    async def dashboard(self, *, days: int = 30) -> dict[str, Any]:
        await self.refresh(days=days)
        end = _day(datetime.now(UTC)) + timedelta(days=1)
        start = end - timedelta(days=days)
        rows = list(
            (
                await self.session.scalars(
                    select(MetricDailyAggregate)
                    .where(
                        MetricDailyAggregate.tenant_id == self.context.tenant_id,
                        MetricDailyAggregate.metric_date >= start,
                        MetricDailyAggregate.metric_date < end,
                    )
                    .order_by(MetricDailyAggregate.metric_date)
                )
            ).all()
        )
        totals = [row for row in rows if row.target_type == "all"]
        targets = [row for row in rows if row.target_type != "all"]
        run_count = sum(row.run_count for row in totals)
        success_count = sum(row.success_count for row in totals)
        error_count = sum(row.error_count for row in totals)
        durations_weighted = [
            row.latency_p95_ms
            for row in totals
            for _ in range(max(1, row.run_count))
            if row.latency_p95_ms is not None
        ]
        top_tools: Counter[str] = Counter()
        for row in totals:
            for item in row.top_tools:
                top_tools[str(item["name"])] += int(item["count"])

        names = await self._target_names()
        target_totals: dict[tuple[str, uuid.UUID], dict[str, Any]] = {}
        for row in targets:
            assert row.target_id is not None
            key = (row.target_type, row.target_id)
            item = target_totals.setdefault(
                key,
                {
                    "target_type": row.target_type,
                    "target_id": row.target_id,
                    "name": names.get(key, str(row.target_id)),
                    "run_count": 0,
                    "success_count": 0,
                    "error_count": 0,
                    "approval_waits": 0,
                    "latency_p95_ms": None,
                },
            )
            item["run_count"] += row.run_count
            item["success_count"] += row.success_count
            item["error_count"] += row.error_count
            item["approval_waits"] += row.approval_waits
            if row.latency_p95_ms is not None:
                item["latency_p95_ms"] = max(item["latency_p95_ms"] or 0, row.latency_p95_ms)
        for item in target_totals.values():
            item["success_rate"] = (
                item["success_count"] / item["run_count"] if item["run_count"] else 0
            )

        return {
            "range_days": days,
            "generated_at": datetime.now(UTC),
            "kpis": {
                "runs": run_count,
                "success_rate": success_count / run_count if run_count else 0,
                "error_rate": error_count / run_count if run_count else 0,
                "latency_p95_ms": _percentile(durations_weighted, 0.95),
                "input_tokens": sum(row.input_tokens for row in totals),
                "output_tokens": sum(row.output_tokens for row in totals),
                "estimated_cost_usd": sum(row.estimated_cost_usd for row in totals),
                "approval_waits": sum(row.approval_waits for row in totals),
                "unique_sessions": sum(row.unique_sessions for row in totals),
            },
            "daily": [
                {
                    "date": row.metric_date,
                    "runs": row.run_count,
                    "success_count": row.success_count,
                    "error_count": row.error_count,
                    "latency_p50_ms": row.latency_p50_ms,
                    "latency_p95_ms": row.latency_p95_ms,
                }
                for row in totals
            ],
            "top_targets": sorted(
                target_totals.values(), key=lambda item: item["run_count"], reverse=True
            )[:20],
            "top_tools": [
                {"name": name, "count": count} for name, count in top_tools.most_common(10)
            ],
        }

    async def _target_names(self) -> dict[tuple[str, uuid.UUID], str]:
        output: dict[tuple[str, uuid.UUID], str] = {}
        agents = await self.session.scalars(
            select(AgentConfig).where(AgentConfig.tenant_id == self.context.tenant_id)
        )
        output.update({("agent", row.id): row.name for row in agents.all()})
        teams = await self.session.scalars(
            select(TeamConfig).where(TeamConfig.tenant_id == self.context.tenant_id)
        )
        output.update({("team", row.id): row.name for row in teams.all()})
        workflows = await self.session.scalars(
            select(WorkflowConfig).where(WorkflowConfig.tenant_id == self.context.tenant_id)
        )
        output.update({("workflow", row.id): row.name for row in workflows.all()})
        return output
