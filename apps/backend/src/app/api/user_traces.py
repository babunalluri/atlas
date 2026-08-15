"""End-user traces: the signed-in user's team/chat runs only."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.traces import _span, _summary
from app.auth.dependencies import require_roles
from app.db.models import (
    AgentConfig,
    ConversationSession,
    Role,
    TeamConfig,
    TraceRecord,
    WorkflowConfig,
)
from app.db.session import tenant_session
from app.observability.repository import TraceRepository
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/api/me/traces", tags=["user-traces"])
MeContext = Annotated[
    TenantContext,
    Depends(require_roles(Role.platform_admin, Role.tenant_admin, Role.end_user)),
]
TenantSession = Annotated[AsyncSession, Depends(tenant_session)]


def _trace_error(trace: TraceRecord) -> str | None:
    if not isinstance(trace.output, dict):
        return None
    error = trace.output.get("error")
    if isinstance(error, str) and error.strip():
        return error.strip()
    return None


async def _session_titles(
    session: AsyncSession,
    context: TenantContext,
    traces: list[TraceRecord],
) -> dict[uuid.UUID, str]:
    ids = {trace.session_id for trace in traces}
    if not ids:
        return {}
    rows = await session.scalars(
        select(ConversationSession).where(
            ConversationSession.tenant_id == context.tenant_id,
            ConversationSession.id.in_(ids),
        )
    )
    return {
        row.id: (row.title or "").strip()
        for row in rows
        if (row.title or "").strip()
    }


async def _target_names(
    session: AsyncSession,
    context: TenantContext,
    traces: list[TraceRecord],
) -> dict[tuple[str, uuid.UUID], str]:
    names: dict[tuple[str, uuid.UUID], str] = {}
    grouped: dict[str, set[uuid.UUID]] = {"agent": set(), "team": set(), "workflow": set()}
    for trace in traces:
        if trace.target_type in grouped:
            grouped[trace.target_type].add(trace.target_id)
    lookups = (
        ("agent", AgentConfig, grouped["agent"]),
        ("team", TeamConfig, grouped["team"]),
        ("workflow", WorkflowConfig, grouped["workflow"]),
    )
    for kind, model, ids in lookups:
        if not ids:
            continue
        rows = await session.scalars(
            select(model).where(
                model.tenant_id == context.tenant_id,
                model.id.in_(ids),
            )
        )
        for row in rows:
            names[(kind, row.id)] = row.name
    return names


def _user_summary(
    trace: TraceRecord,
    span_count: int,
    *,
    session_title: str | None,
    target_name: str | None,
) -> dict[str, Any]:
    return {
        **_summary(trace, span_count),
        "session_title": session_title,
        "target_name": target_name,
        "error": _trace_error(trace),
    }


@router.get("")
async def list_my_traces(
    context: MeContext,
    session: TenantSession,
    status: str | None = Query(default=None, max_length=32),
    target_type: str | None = Query(default=None, pattern="^(agent|team|workflow)$"),
    limit: int = Query(default=100, ge=1, le=250),
) -> list[dict[str, Any]]:
    repo = TraceRepository(session, context)
    traces = list(
        await repo.list(
            status=status,
            target_type=target_type,
            user_id=context.user_id,
            limit=limit,
        )
    )
    titles = await _session_titles(session, context, traces)
    names = await _target_names(session, context, traces)
    return [
        _user_summary(
            trace,
            len(await repo.spans(trace.id)),
            session_title=titles.get(trace.session_id),
            target_name=names.get((trace.target_type, trace.target_id)),
        )
        for trace in traces
    ]


@router.get("/{trace_id}")
async def get_my_trace(
    trace_id: uuid.UUID,
    context: MeContext,
    session: TenantSession,
) -> dict[str, Any]:
    repo = TraceRepository(session, context)
    trace = await repo.get(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    if trace.user_id != context.user_id:
        raise HTTPException(status_code=403, detail="Cannot read another user's traces")
    spans = await repo.spans(trace.id)
    titles = await _session_titles(session, context, [trace])
    names = await _target_names(session, context, [trace])
    return {
        **_user_summary(
            trace,
            len(spans),
            session_title=titles.get(trace.session_id),
            target_name=names.get((trace.target_type, trace.target_id)),
        ),
        "input": trace.input,
        "output": trace.output,
        "metadata": trace.metadata_,
        "spans": [_span(span) for span in spans],
    }
