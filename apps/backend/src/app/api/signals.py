"""Admin signal engine API — metrics board + entry publish."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.db.models import Role
from app.db.session import SessionFactory, apply_tenant_guc, tenant_session
from app.domains import signal_engine_cache as cache
from app.domains.signal_engine import (
    STREAM_INTERVAL_MS,
    SignalEngineService,
    seed_stream_cold_frame,
    state_for_stream,
    stream_frame_from_cache,
)
from app.domains.signal_engine_worker import refresh_tenant_snapshot
from app.domains.sse_frames import SSE_KEEPALIVE, stream_revision
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/signals", tags=["admin-signals"])
# Strong refs so background cold refreshes are not GC'd mid-flight.
_BG_SNAPSHOT_TASKS: set[asyncio.Task[Any]] = set()


def _track_bg_snapshot(task: asyncio.Task[Any]) -> None:
    _BG_SNAPSHOT_TASKS.add(task)
    task.add_done_callback(_BG_SNAPSHOT_TASKS.discard)


AdminContext = Annotated[
    TenantContext,
    Depends(
        require_roles(Role.platform_admin, Role.tenant_admin)
    ),
]
# Org-wide desk: end users may read config + stream; writes stay admin-only.
ViewerContext = Annotated[
    TenantContext,
    Depends(
        require_roles(Role.platform_admin, Role.tenant_admin, Role.end_user)
    ),
]
TenantSession = Annotated[AsyncSession, Depends(tenant_session)]


class SignalPublishIn(BaseModel):
    title: str | None = Field(
        default=None,
        max_length=200,
        description="Notification title; defaults to 'New trading signal'.",
    )
    body: str | None = Field(
        default=None,
        max_length=2000,
        description="Notification body; defaults to the entry line label.",
    )


class SignalConfigPatchIn(BaseModel):
    underlying_symbol: str | None = None
    underlying_label: str | None = None
    fut_symbol: str | None = None
    ce_symbol: str | None = None
    pe_symbol: str | None = None
    crude_symbol: str | None = None
    india_vix_symbol: str | None = None
    strike_step: int | None = None
    pcr: float | None = None
    max_pain: float | None = None
    ivp: float | None = None
    dow_change_pct: float | None = None
    oi_pct_chg: float | None = None
    iv_chg: float | None = None
    india_vix: float | None = None
    fii_net: float | None = None
    entry_ce_premium: float | None = None
    entry_pe_premium: float | None = None
    exit_pct: float | None = None
    mock: bool | None = None
    engine_enabled: bool | None = None


@router.get("/config")
async def get_signal_config(
    context: ViewerContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Selected underlying, F&O symbols, and manual metrics (org-wide read)."""
    return await SignalEngineService(session, context).get_admin_config()


@router.patch("/config")
async def patch_signal_config(
    body: SignalConfigPatchIn,
    context: AdminContext,
    session: TenantSession,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    patch = body.model_dump(exclude_unset=True)
    result = await SignalEngineService(session, context).update_admin_config(patch)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Update failed"))
    # After the request transaction commits: warm Redis so SSE reflects the new
    # parameters (overrides, symbols, Start) without waiting on a cold tick.
    cfg = result.get("config") if isinstance(result.get("config"), dict) else {}
    if patch.get("engine_enabled") is True or (
        cfg.get("engine_enabled") and patch.get("engine_enabled") is not False
    ):
        background_tasks.add_task(
            refresh_tenant_snapshot,
            context.tenant_id,
            auth_org_id=context.auth_org_id,
        )
    return result


@router.get("/state")
async def get_signal_state(
    context: ViewerContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Single snapshot (prefer GET /stream for live desk)."""
    service = SignalEngineService(session, context)
    return await state_for_stream(service)


@router.get("/stream")
async def stream_signal_state(
    request: Request,
    context: ViewerContext,
    instrument: str | None = Query(
        default=None,
        description="Matrix row to watch (e.g. NSE:NIFTY 50). Defaults to admin primary.",
    ),
) -> StreamingResponse:
    """Server-sent events: ~8 Hz metric snapshots with coalesced engine ticks."""

    async def event_stream() -> AsyncIterator[bytes]:
        tenant_key = str(context.tenant_id)
        last_rev: tuple[Any, ...] | None = None
        cleared_stopped_watcher = False
        selected = (instrument or "").strip() or None
        try:
            while True:
                if await request.is_disconnected():
                    break
                # Steady state: Redis only (no Postgres). Cold path opens a
                # short-lived session to seed starting / engine_enabled, then
                # hands compute to refresh_tenant_snapshot (does not await state()).
                payload = await stream_frame_from_cache(
                    tenant_key, instrument=selected
                )
                if payload is None:
                    async with SessionFactory() as session:
                        async with session.begin():
                            await apply_tenant_guc(session, context.tenant_id)
                            service = SignalEngineService(session, context)
                            payload, should_refresh = await seed_stream_cold_frame(
                                service
                            )
                    if should_refresh:
                        task = asyncio.create_task(
                            refresh_tenant_snapshot(
                                context.tenant_id,
                                auth_org_id=context.auth_org_id,
                            )
                        )
                        _track_bg_snapshot(task)
                if not payload.get("engine_enabled", False):
                    if not cleared_stopped_watcher:
                        await cache.clear_watcher(tenant_key)
                        cleared_stopped_watcher = True
                else:
                    cleared_stopped_watcher = False
                    await cache.touch_watcher(tenant_key, instrument=selected)
                rev = stream_revision(payload)
                if rev == last_rev:
                    # Worker has not published a new snapshot — keepalive only
                    # so React does not re-render identical boards at 8 Hz.
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


@router.post("/publish")
async def publish_signal_entry(
    body: SignalPublishIn,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """When entry conditions pass, fan-out in-app notification to all active users."""
    return await SignalEngineService(session, context).publish_entry(
        title=body.title,
        body=body.body,
    )
