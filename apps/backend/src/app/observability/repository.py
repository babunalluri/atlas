from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ConversationSession, TraceRecord, TraceSpan
from app.observability.tracing import redact
from app.tenancy.context import TenantContext

TERMINAL_EVENTS = {
    "RunCompleted": "completed",
    "RunError": "error",
    "RunCancelled": "cancelled",
    "RunPaused": "paused",
}


def _elapsed_ms(started_at: datetime, ended_at: datetime) -> int:
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    return max(0, int((ended_at - started_at).total_seconds() * 1000))


class TraceRepository:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        session_tenant = session.info.get("tenant_id")
        if session_tenant is not None and session_tenant != context.tenant_id:
            raise RuntimeError("Database session tenant does not match request tenant")
        self.session = session
        self.context = context

    async def start(
        self,
        *,
        conversation: ConversationSession,
        target_id: uuid.UUID,
        version_id: uuid.UUID,
        name: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> TraceRecord:
        now = datetime.now(UTC)
        trace = TraceRecord(
            id=uuid.uuid4(),
            tenant_id=self.context.tenant_id,
            session_id=conversation.id,
            external_session_id=conversation.external_session_id,
            target_type=conversation.target_type,
            target_id=target_id,
            version_id=version_id,
            user_id=self.context.user_id,
            name=name[:255],
            status="running",
            input={"message": message},
            metadata_=redact(metadata or {}),
            started_at=now,
        )
        self.session.add(trace)
        await self.session.flush()
        self.session.add(
            TraceSpan(
                id=uuid.uuid4(),
                tenant_id=self.context.tenant_id,
                trace_id=trace.id,
                name=name[:255],
                kind="run",
                status="running",
                sequence=0,
                input={"message": message},
                attributes={"target_type": conversation.target_type},
                started_at=now,
            )
        )
        await self.session.flush()
        return trace

    async def get(self, trace_id: uuid.UUID) -> TraceRecord | None:
        return await self.session.scalar(
            select(TraceRecord).where(
                TraceRecord.id == trace_id,
                TraceRecord.tenant_id == self.context.tenant_id,
            )
        )

    async def list(
        self,
        *,
        status: str | None = None,
        target_type: str | None = None,
        external_session_id: str | None = None,
        user_id: str | None = None,
        limit: int = 100,
    ) -> Sequence[TraceRecord]:
        statement = select(TraceRecord).where(TraceRecord.tenant_id == self.context.tenant_id)
        if status:
            statement = statement.where(TraceRecord.status == status)
        if target_type:
            statement = statement.where(TraceRecord.target_type == target_type)
        if external_session_id:
            statement = statement.where(TraceRecord.external_session_id == external_session_id)
        if user_id:
            statement = statement.where(TraceRecord.user_id == user_id)
        rows = await self.session.scalars(
            statement.order_by(TraceRecord.started_at.desc()).limit(limit)
        )
        return rows.all()

    async def spans(self, trace_id: uuid.UUID) -> Sequence[TraceSpan]:
        rows = await self.session.scalars(
            select(TraceSpan)
            .where(
                TraceSpan.trace_id == trace_id,
                TraceSpan.tenant_id == self.context.tenant_id,
            )
            .order_by(TraceSpan.sequence, TraceSpan.started_at)
        )
        return rows.all()

    async def record_event(
        self, trace_id: uuid.UUID, payload: dict[str, Any]
    ) -> TraceRecord | None:
        trace = await self.get(trace_id)
        if trace is None:
            return None
        now = datetime.now(UTC)
        event_name = str(payload.get("event") or payload.get("type") or "RunEvent")
        safe_payload = redact(payload)
        run_id = payload.get("run_id")
        if run_id:
            trace.run_id = str(run_id)[:255]
        trace.status = TERMINAL_EVENTS.get(event_name, "running")

        root = await self.session.scalar(
            select(TraceSpan).where(
                TraceSpan.tenant_id == self.context.tenant_id,
                TraceSpan.trace_id == trace.id,
                TraceSpan.parent_span_id.is_(None),
                TraceSpan.kind == "run",
            )
        )
        sequence = (
            await self.session.scalar(
                select(func.coalesce(func.max(TraceSpan.sequence), 0)).where(
                    TraceSpan.tenant_id == self.context.tenant_id,
                    TraceSpan.trace_id == trace.id,
                )
            )
            or 0
        ) + 1
        # Streaming token/content events spam the span tree (0 ms each). Keep
        # accumulated text on the root span, but only persist meaningful spans.
        original_name = str(
            payload.get("original_event") or event_name or "RunEvent"
        )
        skip_span = event_name in {
            "RunContent",
            "RunContentCompleted",
            "RunIntermediateContent",
            "RunStarted",
            "TeamRunContent",
            "TeamRunContentCompleted",
            "TeamRunStarted",
        } or original_name in {
            "TeamRunContent",
            "TeamRunContentCompleted",
            "TeamRunStarted",
        }
        if event_name == "RunContent" and isinstance(safe_payload, dict):
            chunk = safe_payload.get("content")
            if chunk is not None and root is not None:
                previous = ""
                if isinstance(root.output, dict):
                    previous = str(root.output.get("content") or "")
                root.output = {
                    **(root.output or {}),
                    "content": f"{previous}{chunk}",
                }
        kind = "event"
        lowered = event_name.lower()
        if "tool" in lowered:
            kind = "tool"
        elif "model" in lowered:
            kind = "model"
        elif "team" in lowered:
            kind = "team"
        error = None
        if event_name == "RunError":
            error = str(payload.get("error") or payload.get("content") or "Run failed")[
                :2000
            ]
        if not skip_span:
            self.session.add(
                TraceSpan(
                    id=uuid.uuid4(),
                    tenant_id=self.context.tenant_id,
                    trace_id=trace.id,
                    parent_span_id=root.id if root else None,
                    name=event_name[:255],
                    kind=kind,
                    status=TERMINAL_EVENTS.get(event_name, "completed"),
                    sequence=sequence,
                    attributes=safe_payload if isinstance(safe_payload, dict) else {},
                    output={"content": safe_payload.get("content")}
                    if isinstance(safe_payload, dict)
                    and safe_payload.get("content") is not None
                    else {},
                    error=error,
                    started_at=now,
                    ended_at=now,
                    duration_ms=0,
                )
            )
        if event_name in TERMINAL_EVENTS:
            trace.ended_at = now
            trace.duration_ms = _elapsed_ms(trace.started_at, now)
            if event_name == "RunCompleted":
                completed_output = {
                    key: safe_payload[key]
                    for key in ("content", "run_id", "metrics")
                    if isinstance(safe_payload, dict) and key in safe_payload
                }
                # Streamed RunContent accumulates on the root span. Terminal
                # RunCompleted events often omit content — do not wipe it.
                if (
                    "content" not in completed_output
                    and root is not None
                    and isinstance(root.output, dict)
                    and root.output.get("content") not in (None, "")
                ):
                    completed_output["content"] = root.output["content"]
                trace.output = completed_output
            elif error:
                trace.output = {"error": error}
            if root:
                root.status = trace.status
                root.ended_at = now
                root.duration_ms = trace.duration_ms
                if event_name == "RunCompleted" and trace.output:
                    # Merge so we keep any prior streamed fields not in terminal payload.
                    prior = root.output if isinstance(root.output, dict) else {}
                    root.output = {**prior, **trace.output}
                else:
                    root.output = trace.output
                root.error = error
        await self.session.flush()
        return trace

    async def fail(self, trace_id: uuid.UUID, message: str) -> None:
        await self.record_event(
            trace_id,
            {"event": "RunError", "error": message[:2000]},
        )
