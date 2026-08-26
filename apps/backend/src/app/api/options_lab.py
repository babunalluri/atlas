"""Admin Options Lab API — live option chain for operators."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
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
    chain_frame_from_cache,
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
    mode: str = Query("full", pattern="^(fast|full)$"),
) -> dict[str, Any]:
    """Index/equity F&O screener — fast=spot/ATM, full=PCR/IV/OI enrich."""
    return await OptionsLabService(session, context).screener_snapshot(
        universe=universe,
        mode=mode,
    )


@router.get("/flows")
async def get_options_lab_flows(
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """FII/DII net flows for Options Lab (no Signal Engine Start required)."""
    return await OptionsLabService(session, context).flows_snapshot()


@router.get("/gtts")
async def list_options_lab_gtts(
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """List open GTTs via bound kite list_gtts (Live/Signals)."""
    return await OptionsLabService(session, context).list_gtts()


@router.delete("/gtts/{trigger_id}")
async def delete_options_lab_gtt(
    trigger_id: str,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Cancel a GTT via bound kite delete_gtt (mutating)."""
    return await OptionsLabService(session, context).delete_gtt(trigger_id)

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
                # Steady state: Redis only (no Postgres). Cold path opens a
                # short-lived session when fingerprint or snapshot is missing.
                payload = await chain_frame_from_cache(tenant_key, wings=wings)
                if payload is None:
                    await ol_cache.touch_watcher(tenant_key, wings=wings)
                    async with SessionFactory() as session:
                        async with session.begin():
                            await apply_tenant_guc(session, context.tenant_id)
                            service = OptionsLabService(session, context)
                            payload = await chain_state_for_stream(
                                service, wings=wings
                            )
                    await ol_cache.touch_watcher(
                        tenant_key,
                        wings=int(payload.get("wings", wings)),
                    )
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


class OptionsLabStrategyLegIn(BaseModel):
    id: str | None = None
    side: str
    type: str | None = None
    strike: int | float | None = None
    qty: float = 1
    entry_premium: float | None = None
    premium: float | None = None
    symbol: str | None = None


class OptionsLabMarginsIn(BaseModel):
    legs: list[OptionsLabStrategyLegIn]
    lot_size: int | None = None
    product: str = "NRML"
    underlying_symbol: str | None = None
    heuristic: dict[str, Any] | None = None
    mock: bool | None = None
    basket: bool = False


class OptionsLabOrdersIn(BaseModel):
    legs: list[OptionsLabStrategyLegIn]
    confirm: bool = False
    live: bool = False
    lot_size: int | None = None
    product: str = "NRML"
    order_type: str = "LIMIT"
    name: str | None = None
    underlying_symbol: str | None = None
    save_draft: bool = True
    mock: bool | None = None
    tag: str | None = None
    stop_loss_pct: float | None = Field(default=None, ge=0.5, le=90)
    target_pct: float | None = Field(default=None, ge=0.5, le=200)
    basket: bool = False


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


@router.get("/broker-reconcile")
async def options_lab_broker_reconcile(
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Read-only broker positions + available margin vs Lab books and bot opens."""
    return await OptionsLabService(session, context).broker_reconcile()


@router.post("/margins")
async def options_lab_strategy_margins(
    body: OptionsLabMarginsIn,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Broker order-margin + available funds for a builder strategy (fallback heuristic)."""
    return await OptionsLabService(session, context).strategy_margins(
        body.model_dump(exclude_none=True)
    )


@router.post("/orders")
async def options_lab_place_strategy_orders(
    body: OptionsLabOrdersIn,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Place multi-leg orders via bound Live/Paper place_order (requires confirm=true)."""
    return await OptionsLabService(session, context).place_strategy_orders(
        body.model_dump(exclude_none=True)
    )


class OptionsLabBacktestLegIn(BaseModel):
    id: str | None = None
    side: str
    type: str
    strike: int | float
    qty: float = 1
    premium: float | None = None
    entry_premium: float | None = None
    symbol: str | None = None


class OptionsLabBacktestCreateIn(BaseModel):
    name: str | None = None
    legs: list[OptionsLabBacktestLegIn]
    spot: float
    days: int = 10
    shock_pct: float = 2.0
    path_bias: str = "flat"
    strike_step: int | None = None
    underlying_symbol: str | None = None
    underlying_label: str | None = None
    use_historical: bool = False
    use_marks: bool = False
    iv_pct: float | None = None
    entry_dte: float | None = None
    closes: list[float] | None = Field(default=None, max_length=400)


class OptionsLabBacktestRunIn(BaseModel):
    legs: list[OptionsLabBacktestLegIn]
    spot: float
    days: int = 10
    shock_pct: float = 2.0
    path_bias: str = "flat"
    use_historical: bool = False
    use_marks: bool = False
    iv_pct: float | None = None
    entry_dte: float | None = None
    closes: list[float] | None = Field(default=None, max_length=400)


class OptionsLabBacktestSummaryIn(BaseModel):
    ids: list[str] | None = None
    limit: int = 5


@router.get("/backtests")
async def list_options_lab_backtests(
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """List saved model backtests for this tenant."""
    return await OptionsLabService(session, context).list_backtests()


@router.post("/backtests")
async def create_options_lab_backtest(
    body: OptionsLabBacktestCreateIn,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Run + persist a model backtest (session store)."""
    return await OptionsLabService(session, context).create_backtest(
        body.model_dump(exclude_none=True)
    )


@router.post("/backtests/run")
async def run_options_lab_backtest(
    body: OptionsLabBacktestRunIn,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Run a model backtest without saving."""
    return await OptionsLabService(session, context).run_model_backtest(
        body.model_dump(exclude_none=True)
    )


@router.post("/backtests/summary")
async def summarize_options_lab_backtests(
    body: OptionsLabBacktestSummaryIn,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Multi-run portfolio summary of saved model backtests."""
    return await OptionsLabService(session, context).summarize_backtests(
        ids=body.ids,
        limit=body.limit,
    )


@router.get("/backtests/{backtest_id}")
async def get_options_lab_backtest(
    backtest_id: str,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Fetch one saved backtest including shocks / equity."""
    return await OptionsLabService(session, context).get_backtest(backtest_id)


@router.delete("/backtests/{backtest_id}")
async def delete_options_lab_backtest(
    backtest_id: str,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Delete a saved model backtest."""
    return await OptionsLabService(session, context).delete_backtest(backtest_id)


class OptionsLabBotEntryIn(BaseModel):
    min_ivp: float | None = None
    max_ivp: float | None = None
    min_pcr: float | None = None
    max_pcr: float | None = None
    max_dte: float | None = None


class OptionsLabBotScheduleIn(BaseModel):
    days: list[int] | None = None
    window_start: str | None = None
    window_end: str | None = None


class OptionsLabBotCreateIn(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    kill: bool | None = None
    mode: str | None = None
    template: str | None = None
    backtest_id: str | None = None
    underlying_symbol: str | None = None
    width_steps: int | None = None
    profit_pct: float | None = None
    stop_pct: float | None = None
    avoid_events: bool | None = None
    max_dte_hold: int | None = None
    clear_open_position: bool | None = None
    schedule: OptionsLabBotScheduleIn | None = None
    entry: OptionsLabBotEntryIn | None = None
    cooldown_sec: int | None = None
    max_runs_per_day: int | None = None
    source: str | None = None


class OptionsLabBotUpdateIn(OptionsLabBotCreateIn):
    pass


class OptionsLabBotRunIn(BaseModel):
    confirm: bool = False
    auto: bool = False


@router.get("/bots")
async def list_options_lab_bots(
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """List persisted Options Lab bots for this tenant."""
    from app.core.settings import get_settings

    out = await OptionsLabService(session, context).list_bots()
    out["worker_enabled"] = bool(get_settings().options_lab_bots_enabled)
    return out


@router.post("/bots")
async def create_options_lab_bot(
    body: OptionsLabBotCreateIn,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Create a bot (optional backtest_id handoff)."""
    payload = body.model_dump(exclude_none=True)
    payload.setdefault("enabled", False)
    payload.setdefault("kill", False)
    payload.setdefault("mode", "paper")
    return await OptionsLabService(session, context).create_bot(payload)


@router.post("/bots/evaluate")
async def evaluate_options_lab_bots(
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Nudge: evaluate due armed paper bots for this tenant."""
    return await OptionsLabService(session, context).evaluate_armed_bots()


@router.get("/bots/{bot_id}")
async def get_options_lab_bot(
    bot_id: str,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    return await OptionsLabService(session, context).get_bot(bot_id)


@router.patch("/bots/{bot_id}")
async def update_options_lab_bot(
    bot_id: str,
    body: OptionsLabBotUpdateIn,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    return await OptionsLabService(session, context).update_bot(
        bot_id, body.model_dump(exclude_unset=True)
    )


@router.delete("/bots/{bot_id}")
async def delete_options_lab_bot(
    bot_id: str,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    return await OptionsLabService(session, context).delete_bot(bot_id)


@router.post("/bots/{bot_id}/run")
async def run_options_lab_bot(
    bot_id: str,
    body: OptionsLabBotRunIn,
    context: AdminContext,
    session: TenantSession,
) -> dict[str, Any]:
    """Run once (HITL). Live requires confirm; auto flag is for worker/tests."""
    return await OptionsLabService(session, context).run_bot(
        bot_id,
        auto=bool(body.auto),
        confirm=bool(body.confirm),
    )
