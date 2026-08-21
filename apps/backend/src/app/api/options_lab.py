"""Admin Options Lab API — live option chain for operators."""

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
from app.domains import options_lab_cache as ol_cache
from app.domains.options_lab import (
    DEFAULT_WINGS,
    MAX_WINGS,
    MIN_WINGS,
    OptionsLabService,
    chain_state_for_stream,
)
from app.domains.signal_engine_constants import STREAM_INTERVAL_MS
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/options-lab", tags=["admin-options-lab"])
AdminContext = Annotated[
    TenantContext,
    Depends(require_roles(Role.platform_admin, Role.tenant_admin)),
]
StreamAdminContext = Annotated[
    TenantContext,
    Depends(require_roles(Role.platform_admin, Role.tenant_admin)),
]
TenantSession = Annotated[AsyncSession, Depends(tenant_session)]


class OptionsLabConfigPatchIn(BaseModel):
    underlying_symbol: str | None = None
    underlying_label: str | None = None
    fut_symbol: str | None = None
    strike_step: int | None = None
    mock: bool | None = None


@router.get("/config")
async def get_options_lab_config(
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Options Lab setup — independent from Signal engine admin config."""
    return await OptionsLabService(session, context).get_admin_config()


@router.patch("/config")
async def patch_options_lab_config(
    body: OptionsLabConfigPatchIn,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Update Options Lab setup without touching Signal engine settings."""
    patch = body.model_dump(exclude_unset=True)
    return await OptionsLabService(session, context).update_admin_config(patch)


@router.post("/reset-oi-baseline")
async def reset_options_lab_oi_baseline(
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Reset intraday OI change baseline (IST session)."""
    return await OptionsLabService(session, context).reset_oi_baseline()


@router.post("/reset-screener-baseline")
async def reset_options_lab_screener_baseline(
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Reset screener OI/IV session baselines (IST session)."""
    return await OptionsLabService(session, context).reset_screener_baseline()


@router.post("/reset-iv-history")
async def reset_options_lab_iv_history(
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Clear stored ATM IV history used for IVP."""
    return await OptionsLabService(session, context).reset_iv_history()


@router.get("/iv-history")
async def get_options_iv_history(
    context: AdminContext,
    session: TenantSession,
    symbol: str = Query(..., min_length=3),
) -> dict[str, Any]:
    """ATM IV daily history and IV percentile for one underlying."""
    return await OptionsLabService(session, context).iv_history(symbol=symbol)


@router.get("/screener")
async def get_options_screener(
    context: AdminContext,
    session: TenantSession,
    universe: str = Query("indices"),
) -> dict[str, Any]:
    """Index F&O screener — PCR, max pain, ATM IV, session OI/IV change."""
    return await OptionsLabService(session, context).screener_snapshot(universe=universe)


@router.get("/chain")
async def get_options_chain(
    context: AdminContext,
    session: TenantSession,
    wings: int = Query(DEFAULT_WINGS, ge=MIN_WINGS, le=MAX_WINGS),
) -> dict[str, Any]:
    """Live CE/PE chain from Kite quotes (ATM ± wings)."""
    return await OptionsLabService(session, context).chain_snapshot(wings=wings)


@router.get("/stream")
async def stream_options_chain(
    request: Request,
    context: StreamAdminContext,
    wings: int = Query(DEFAULT_WINGS, ge=MIN_WINGS, le=MAX_WINGS),
) -> StreamingResponse:
    """Server-sent events: ~8 Hz chain snapshots with coalesced ticks."""

    async def event_stream() -> AsyncIterator[bytes]:
        tenant_key = str(context.tenant_id)
        try:
            while True:
                if await request.is_disconnected():
                    break
                await ol_cache.touch_watcher(tenant_key, wings=wings)
                async with SessionFactory() as session:
                    async with session.begin():
                        await apply_tenant_guc(session, context.tenant_id)
                        service = OptionsLabService(session, context)
                        payload = await chain_state_for_stream(service, wings=wings)
                await ol_cache.touch_watcher(tenant_key, wings=wings)
                frame = json.dumps(payload, separators=(",", ":"))
                yield f"data: {frame}\n\n".encode()
                await asyncio.sleep(STREAM_INTERVAL_MS / 1000)
        except asyncio.CancelledError:
            raise
        finally:
            await ol_cache.clear_watcher(tenant_key)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class OptionsLabPortfolioLegIn(BaseModel):
    id: str | None = None
    side: str
    type: str
    strike: int
    qty: float = 1
    entry_premium: float
    symbol: str | None = None


class OptionsLabPortfolioCreateIn(BaseModel):
    name: str
    underlying_symbol: str | None = None
    underlying_label: str | None = None
    fut_symbol: str | None = None
    strike_step: int | None = None
    source: str | None = None
    legs: list[OptionsLabPortfolioLegIn]


class OptionsLabPortfolioPatchIn(BaseModel):
    name: str | None = None
    underlying_symbol: str | None = None
    underlying_label: str | None = None
    fut_symbol: str | None = None
    strike_step: int | None = None
    legs: list[OptionsLabPortfolioLegIn] | None = None


class OptionsLabPortfolioImportIn(BaseModel):
    name: str | None = None


@router.get("/portfolios")
async def list_options_portfolios(
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """List draft Options Lab portfolios for this tenant."""
    return await OptionsLabService(session, context).list_portfolios()


@router.post("/portfolios")
async def create_options_portfolio(
    body: OptionsLabPortfolioCreateIn,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Create a draft portfolio from builder legs or manual input."""
    return await OptionsLabService(session, context).create_portfolio(body.model_dump())


@router.patch("/portfolios/{portfolio_id}")
async def patch_options_portfolio(
    portfolio_id: str,
    body: OptionsLabPortfolioPatchIn,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Update portfolio name or legs."""
    patch = body.model_dump(exclude_unset=True)
    return await OptionsLabService(session, context).update_portfolio(portfolio_id, patch)


@router.delete("/portfolios/{portfolio_id}")
async def delete_options_portfolio(
    portfolio_id: str,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Remove a draft portfolio."""
    return await OptionsLabService(session, context).delete_portfolio(portfolio_id)


@router.get("/portfolios/{portfolio_id}/mark")
async def mark_options_portfolio(
    portfolio_id: str,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Mark draft portfolio legs to market using live or mock quotes."""
    return await OptionsLabService(session, context).mark_portfolio(portfolio_id)


@router.post("/portfolios/import-kite")
async def import_options_portfolio_from_kite(
    body: OptionsLabPortfolioImportIn,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Import open F&O option positions from Kite into a draft portfolio."""
    return await OptionsLabService(session, context).import_kite_portfolio(name=body.name)
