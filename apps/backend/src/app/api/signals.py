"""Admin signal engine API — metrics board + entry publish."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
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
    state_for_stream,
)
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/signals", tags=["admin-signals"])
AdminContext = Annotated[
    TenantContext,
    Depends(
        require_roles(Role.platform_admin, Role.tenant_admin)
    ),
]
StreamAdminContext = Annotated[
    TenantContext,
    Depends(require_roles(Role.platform_admin, Role.tenant_admin)),
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
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Admin-selected underlying, F&O symbols, and manual metrics."""
    return await SignalEngineService(session, context).get_admin_config()


@router.patch("/config")
async def patch_signal_config(
    body: SignalConfigPatchIn,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    patch = body.model_dump(exclude_unset=True)
    result = await SignalEngineService(session, context).update_admin_config(patch)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Update failed"))
    return result


@router.get("/state")
async def get_signal_state(
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Single snapshot (prefer GET /stream for live admin desk)."""
    service = SignalEngineService(session, context)
    return await state_for_stream(service)


@router.get("/stream")
async def stream_signal_state(
    request: Request,
    context: StreamAdminContext,
) -> StreamingResponse:
    """Server-sent events: ~8 Hz metric snapshots with coalesced engine ticks."""

    async def event_stream() -> AsyncIterator[bytes]:
        tenant_key = str(context.tenant_id)
        try:
            while True:
                if await request.is_disconnected():
                    break
                async with SessionFactory() as session:
                    async with session.begin():
                        await apply_tenant_guc(session, context.tenant_id)
                        service = SignalEngineService(session, context)
                        payload = await state_for_stream(service)
                if payload.get("engine_enabled", False):
                    await cache.touch_watcher(tenant_key)
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
