import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_tenant
from app.db.models import TraceRecord, TraceSpan
from app.db.session import tenant_session
from app.observability.repository import TraceRepository
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/api/admin/traces", tags=["traces"])
TenantSession = Annotated[AsyncSession, Depends(tenant_session)]


async def require_trace_reader(
    context: Annotated[TenantContext, Depends(require_tenant)],
) -> TenantContext:
    if context.can_administer() or (
        context.principal_type == "service_account" and context.has_scope("traces:read")
    ):
        return context
    raise HTTPException(status_code=403, detail="Trace access requires tenant administrator role")


TraceContext = Annotated[TenantContext, Depends(require_trace_reader)]


def _summary(trace: TraceRecord, span_count: int) -> dict[str, Any]:
    return {
        "id": trace.id,
        "run_id": trace.run_id,
        "session_id": trace.external_session_id,
        "target_type": trace.target_type,
        "target_id": trace.target_id,
        "version_id": trace.version_id,
        "user_id": trace.user_id,
        "name": trace.name,
        "status": trace.status,
        "started_at": trace.started_at,
        "ended_at": trace.ended_at,
        "duration_ms": trace.duration_ms,
        "span_count": span_count,
    }


def _span(span: TraceSpan) -> dict[str, Any]:
    return {
        "id": span.id,
        "parent_span_id": span.parent_span_id,
        "name": span.name,
        "kind": span.kind,
        "status": span.status,
        "sequence": span.sequence,
        "attributes": span.attributes,
        "input": span.input,
        "output": span.output,
        "error": span.error,
        "started_at": span.started_at,
        "ended_at": span.ended_at,
        "duration_ms": span.duration_ms,
    }


@router.get("")
async def list_traces(
    context: TraceContext,
    session: TenantSession,
    status: str | None = Query(default=None, max_length=32),
    target_type: str | None = Query(default=None, pattern="^(agent|team)$"),
    session_id: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=100, ge=1, le=250),
) -> list[dict[str, Any]]:
    repo = TraceRepository(session, context)
    traces = await repo.list(
        status=status,
        target_type=target_type,
        external_session_id=session_id,
        limit=limit,
    )
    return [_summary(trace, len(await repo.spans(trace.id))) for trace in traces]


@router.get("/{trace_id}")
async def get_trace(
    trace_id: uuid.UUID,
    context: TraceContext,
    session: TenantSession,
) -> dict[str, Any]:
    repo = TraceRepository(session, context)
    trace = await repo.get(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    spans = await repo.spans(trace.id)
    return {
        **_summary(trace, len(spans)),
        "input": trace.input,
        "output": trace.output,
        "metadata": trace.metadata_,
        "spans": [_span(span) for span in spans],
    }
