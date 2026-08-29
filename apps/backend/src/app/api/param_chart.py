"""Admin Param Chart API — monthly OHLC + shared params overlay."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.db.models import Role
from app.db.session import SessionFactory, apply_tenant_guc, tenant_session
from app.domains.param_chart import (
    ParamChartService,
    month_state_for_stream,
    overlay_frame_from_cache,
    refresh_overlay_from_cache,
)
from app.domains.signal_engine_constants import STREAM_INTERVAL_MS
from app.domains.sse_frames import SSE_KEEPALIVE, stream_revision
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/param-chart", tags=["admin-param-chart"])
AdminContext = Annotated[
    TenantContext,
    Depends(require_roles(Role.platform_admin, Role.tenant_admin)),
]
StreamAdminContext = Annotated[
    TenantContext,
    Depends(require_roles(Role.platform_admin, Role.tenant_admin)),
]
TenantSession = Annotated[AsyncSession, Depends(tenant_session)]


class ParamChartConfigPatchIn(BaseModel):
    underlying_symbol: str | None = None
    underlying_label: str | None = None
    fut_symbol: str | None = None
    strike_step: int | None = None
    strike: int | None = None
    entry_ce_premium: float | None = None
    entry_pe_premium: float | None = None
    ce_symbol: str | None = None
    pe_symbol: str | None = None
    year: int | None = None
    month: int | None = None
    interval: str | None = None


@router.get("/config")
async def get_param_chart_config(
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    return await ParamChartService(session, context).get_admin_config()


@router.patch("/config")
async def patch_param_chart_config(
    body: ParamChartConfigPatchIn,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    patch = body.model_dump(exclude_unset=True)
    return await ParamChartService(session, context).update_admin_config(patch)


@router.get("/month")
async def get_param_chart_month(
    context: AdminContext,
    session: TenantSession,
    year: int | None = Query(None, ge=2000, le=2100),
    month: int | None = Query(None, ge=1, le=12),
    interval: str | None = Query(None),
    underlying: str | None = Query(
        None,
        description="Instrument to read (e.g. NSE:NIFTY 50). Defaults to the desk instrument.",
    ),
    refresh: bool = Query(False),
    build_missing: bool = Query(True),
) -> dict[str, Any]:
    return await ParamChartService(session, context).month_state(
        year=year,
        month=month,
        interval=interval,
        underlying=underlying,
        force_refresh=refresh,
        build_missing=build_missing,
    )


@router.post("/persist-metrics")
async def persist_param_chart_metrics(
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Stamp shared checklist metrics into today's Param Chart day card (EOD history)."""
    pack = await ParamChartService(session, context).persist_metrics_from_signal_snapshot(
        force=True
    )
    return {
        "ok": pack is not None,
        "persisted": pack is not None,
        "eod_at": (pack or {}).get("eod_at"),
        "metrics_persisted_at": (pack or {}).get("metrics_persisted_at"),
        "day": (pack or {}).get("today")
        or next(
            (
                d.get("date")
                for d in ((pack or {}).get("days") or [])
                if d.get("metrics")
            ),
            None,
        ),
    }


@router.get("/stream")
async def stream_param_chart(
    request: Request,
    context: StreamAdminContext,
    underlying: str | None = Query(
        None,
        description="Instrument to watch. Defaults to the desk instrument.",
    ),
) -> StreamingResponse:
    """SSE: today overlay from Redis (book + Signal snapshot), ~8 Hz."""

    async def event_stream() -> AsyncIterator[bytes]:
        tenant_key = str(context.tenant_id)
        selected = (underlying or "").strip() or None
        last_rev: tuple[Any, ...] | None = None
        try:
            while True:
                if await request.is_disconnected():
                    break
                payload = await overlay_frame_from_cache(tenant_key, selected)
                if payload is None:
                    payload = await refresh_overlay_from_cache(
                        tenant_key, underlying=selected
                    )
                if payload is None:
                    async with SessionFactory() as session:
                        async with session.begin():
                            await apply_tenant_guc(session, context.tenant_id)
                            payload = await month_state_for_stream(
                                session, context, selected
                            )
                rev = stream_revision(payload)
                if rev == last_rev:
                    yield SSE_KEEPALIVE
                else:
                    last_rev = rev
                    frame = json.dumps(payload, separators=(",", ":"))
                    yield f"data: {frame}\n\n".encode()
                await asyncio.sleep(STREAM_INTERVAL_MS / 1000)
        except asyncio.CancelledError:
            raise

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
