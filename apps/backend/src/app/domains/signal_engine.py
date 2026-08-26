"""Admin signal engine: tiered metric fetch, entry evaluation, publish hooks.

Metrics are admin-only (Signals ops team). End-user desk never loads this module.
UI pushes at ~8×/sec (SSE); broker quotes refresh ~2×/sec; slow sources cache longer.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.factory import AgentFactoryService, McpToolSkipped
from app.core.logging import get_logger
from app.db.repositories import TeamRepository, ToolDefinitionRepository, ToolDefinitionVersionRepository
from app.db.repositories import UserNotificationRepository
from app.domains.desk_snapshot import (
    QUOTE_CAPABILITIES,
    _groww_symbol,
    _looks_like_quote_map,
    _quote_map_rows,
    invoke_tool,
    quote_call_attempts,
    resolve_kite_instrument,
)
from app.domains import signal_engine_cache as cache
from app.domains.signal_engine_constants import (
    BROKER_QUOTE_TTL_MS,
    ENGINE_STARTING_SNAPSHOT_MS,
    SNAPSHOT_FRESH_MS,
    STATE_COMPUTE_TIMEOUT_MS,
    STREAM_COMPUTE_WAIT_MS,
    STREAM_INTERVAL_MS,
    TIER_A_REST_GAP_FILL_MS,
    TIER_TTL_MS,
    Tier,
)
from app.domains.signal_engine_calendar import europe_session_max_abs_chg
from app.domains.signal_engine_chain import build_chain_symbols, chain_metrics_from_quotes
from app.domains.signal_engine_levels import (
    apply_spot_derived_fields,
    chart_timeframe_snapshots,
    contextual_desk_chart_feeds,
    expiry_levels_from_daily,
    intraday_indicators_from_candles,
    levels_from_candles,
    mock_levels,
)
from app.domains.signal_engine_nse import fetch_nse_slow_fields, mock_nse_fields
from app.domains.signal_engine_yahoo import (
    CRYPTO_YAHOO_TICKERS,
    GLOBAL_YAHOO_TICKERS,
    INDEX_KITE_SYMBOLS,
    STOCK_KITE_SYMBOLS,
    TIMING_YAHOO_TICKERS,
    USD_INR_KITE_SYMBOL,
    crypto_max_abs_change,
    fetch_yahoo_changes,
    fetch_yahoo_session_changes,
    mock_yahoo_changes,
)
from app.domains.trade_desk_checklist import CHECKLIST_CATEGORIES, DEFAULT_METRICS, normalize_metrics
from app.tenancy.context import TenantContext

QUOTE_TOOL_PRIORITY = {"get_quote": 0, "get_ltp": 1, "get_ohlc": 2}
SIGNAL_BROKER_CAPABILITIES = (*QUOTE_CAPABILITIES, "get_historical_candles")
IST = timezone(timedelta(hours=5, minutes=30))
ADX_CANDLE_INTERVAL = "15minute"
ADX_LOOKBACK_DAYS = 5
# ~4 months of dailies for expiry levels; 10× vs prior 12-day window — watch Kite quota.
LEVELS_DAILY_HISTORY_DAYS = 120

SIGNAL_TEAM_SLUG = "signals-ops"

logger = get_logger(__name__)

Rule = Literal[
    "lt",
    "gt",
    "lte",
    "gte",
    "eq",
    "abs_lte",
    "below_prev_close",
    "ce_pe_balance",
    "iv_pct_day_high",
    "between",
    "spot_below_max_pain",
    "before_time",
    "info",
]


async def _invalidate_tenant_signal_cache(tenant_id: str) -> None:
    await cache.invalidate_tenant(tenant_id)


def _apply_engine_stopped_overlay(payload: dict[str, Any]) -> dict[str, Any]:
    """Ensure cached/stream payloads reflect a stopped engine."""
    return {
        **payload,
        "engine_enabled": False,
        "engine_active": False,
        "live": False,
        "feed_source": "stopped",
        "live_quote_missing": False,
        "live_warnings": [],
    }


def _engine_starting_payload(config: "SignalEngineConfig") -> dict[str, Any]:
    """Provisional SSE frame so Start never leaves the desk on a stale stopped snapshot."""
    now_ms = int(time.time() * 1000)
    return {
        "engine_enabled": True,
        "engine_active": True,
        "engine_computing": True,
        "live": False,
        "mock": bool(config.mock),
        "feed_source": "starting",
        "live_quote_missing": False,
        "live_warnings": ["Starting engine — warming live quotes…"],
        "metrics": [],
        "passed": 0,
        "evaluable": 0,
        "has_broker": True,
        "team_slug": SIGNAL_TEAM_SLUG,
        "underlying": {
            "symbol": config.underlying_symbol,
            "label": config.underlying_label or config.underlying_symbol or "—",
        },
        "stream": True,
        "computed_at_ms": now_ms,
        "broker_poll_ms": BROKER_QUOTE_TTL_MS,
    }


def _annotate_snapshot_freshness(
    payload: dict[str, Any],
    *,
    computing: bool = False,
) -> dict[str, Any]:
    """Attach age + server-side stale flag. Unknown age stays null (not 'fresh').

    While a refresh is in flight, keep the badge fresh so long sandbox ticks do
    not flip Running → Stale between computes.
    """
    now_ms = int(time.time() * 1000)
    out = dict(payload)
    out["snapshot_fresh_ms"] = SNAPSHOT_FRESH_MS
    out["engine_computing"] = bool(computing)
    computed = out.get("computed_at_ms")
    if computed is None:
        out["data_age_ms"] = None
        out["snapshot_stale"] = None if not computing else False
        return out
    try:
        computed_ms = int(computed)
    except (TypeError, ValueError):
        out["data_age_ms"] = None
        out["snapshot_stale"] = None if not computing else False
        return out
    age = max(0, now_ms - computed_ms)
    out["data_age_ms"] = age
    if computing:
        out["snapshot_stale"] = False
    else:
        out["snapshot_stale"] = age > SNAPSHOT_FRESH_MS
    return out


async def _compute_state_payload(
    service: "SignalEngineService",
    *,
    config: "SignalEngineConfig",
    last_good: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run state() with a hard timeout so sandbox storms cannot hold the lock forever.

    On timeout, prefer the last live snapshot over an empty ``starting`` frame so
    a slow tick cannot wipe CE/PE / VIX / stock rows the desk already had.
    """
    try:
        payload = await asyncio.wait_for(
            service.state(),
            timeout=STATE_COMPUTE_TIMEOUT_MS / 1000,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "signal_state_compute_timeout",
            tenant_id=_tenant_key(service.context),
            timeout_ms=STATE_COMPUTE_TIMEOUT_MS,
        )
        if (
            isinstance(last_good, dict)
            and last_good.get("feed_source") not in (None, "starting", "stopped")
            and (last_good.get("metrics") or last_good.get("passed") is not None)
        ):
            kept = dict(last_good)
            msg = (
                "Engine tick timed out under load — showing last good frame while retrying."
            )
            warnings = [
                msg,
                *[
                    w
                    for w in (kept.get("live_warnings") or [])
                    if "timed out under load" not in str(w)
                ],
            ]
            kept["live_warnings"] = warnings
            kept["engine_computing"] = True
            kept["engine_enabled"] = True
            kept["engine_active"] = True
            return kept
        payload = _engine_starting_payload(config)
        payload["live_warnings"] = [
            "Engine tick timed out under load — retrying. "
            "Close Param Chart / Options Lab or enable Mock if this persists."
        ]
        return payload
    if not config.engine_enabled:
        return _apply_engine_stopped_overlay(payload)
    return {**payload, "computed_at_ms": int(time.time() * 1000)}


async def state_for_stream(
    service: "SignalEngineService",
    *,
    config: SignalEngineConfig | None = None,
) -> dict[str, Any]:
    """Coalesce concurrent stream/poll readers to one engine tick per tenant.

    Pass ``config`` when the caller already loaded it (SSE heartbeat path) so we
    do not walk tool bindings twice per 125ms frame.
    """
    tenant_id = _tenant_key(service.context)
    if config is None:
        config = await service._load_config()
    # Boolean-only cache: Lab/Param Chart patches do not invalidate signal
    # metrics, so we must not stash the full config blob here.
    await cache.set_metric(
        tenant_id, "engine_enabled", "medium", bool(config.engine_enabled)
    )
    computing = await cache.compute_lock_held(tenant_id)

    snapshot = await cache.get_snapshot(tenant_id)
    if snapshot is not None:
        if not config.engine_enabled:
            return _apply_engine_stopped_overlay(snapshot)
        return _annotate_snapshot_freshness(snapshot, computing=computing)

    if await cache.try_compute_lock(tenant_id):
        heartbeat = cache.start_compute_lock_heartbeat(tenant_id)
        try:
            # Re-check under the lock — another writer may have published.
            snapshot = await cache.get_snapshot(tenant_id)
            if snapshot is not None:
                if not config.engine_enabled:
                    return _apply_engine_stopped_overlay(snapshot)
                return _annotate_snapshot_freshness(snapshot, computing=True)
            # True cold start only (no snapshot when the lock was taken). Keep-
            # last-good for refresh timeouts lives in the ticker worker, which
            # calls ``_compute_state_payload`` with the prior live frame.
            if config.engine_enabled:
                starting = _engine_starting_payload(config)
                await cache.set_snapshot(
                    tenant_id,
                    starting,
                    ttl_ms=ENGINE_STARTING_SNAPSHOT_MS,
                    force=True,
                )
            payload = await _compute_state_payload(
                service, config=config, last_good=None
            )
            await cache.set_snapshot(tenant_id, payload)
            return _annotate_snapshot_freshness(payload, computing=False)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            await cache.release_compute_lock(tenant_id)

    # Another reader/worker holds the compute lock — wait for their snapshot
    # instead of immediately starting a duplicate cold state().
    slice_ms = max(STREAM_INTERVAL_MS / 4, 25)
    wait_slices = max(8, int(STREAM_COMPUTE_WAIT_MS / slice_ms))
    for _ in range(wait_slices):
        await asyncio.sleep(slice_ms / 1000)
        snapshot = await cache.get_snapshot(tenant_id)
        if snapshot is not None:
            if not config.engine_enabled:
                return _apply_engine_stopped_overlay(snapshot)
            still = await cache.compute_lock_held(tenant_id)
            return _annotate_snapshot_freshness(snapshot, computing=still)

    # Still no snapshot — emit starting (or stopped) rather than stacking another
    # unbounded cold compute behind a contended sandbox pool.
    if not config.engine_enabled:
        payload = _apply_engine_stopped_overlay(_engine_starting_payload(config))
    else:
        payload = _engine_starting_payload(config)
    await cache.set_snapshot(tenant_id, payload, ttl_ms=ENGINE_STARTING_SNAPSHOT_MS, force=True)
    return _annotate_snapshot_freshness(payload, computing=True)


async def stream_frame_from_cache(tenant_id: str) -> dict[str, Any] | None:
    """Serve one SSE frame from Redis only.

    Returns None when ``engine_enabled`` or the snapshot is missing so the
    caller can open a DB session for the cold path.
    """
    enabled = await cache.get_metric(tenant_id, "engine_enabled")
    snapshot = await cache.get_snapshot(tenant_id)
    if not isinstance(enabled, bool) or snapshot is None:
        return None
    if enabled:
        await cache.touch_watcher(tenant_id)
        computing = await cache.compute_lock_held(tenant_id)
        return _annotate_snapshot_freshness(snapshot, computing=computing)
    return _apply_engine_stopped_overlay(snapshot)

# Admin-selectable underlyings (not hard-coded to NIFTY).
UNDERLYING_PRESETS: list[dict[str, Any]] = [
    {"label": "NIFTY 50", "symbol": "NSE:NIFTY 50", "strike_step": 50},
    {"label": "BANKNIFTY", "symbol": "NSE:NIFTY BANK", "strike_step": 100},
    {"label": "FINNIFTY", "symbol": "NSE:NIFTY FIN SERVICE", "strike_step": 50},
    {"label": "NIFTYNXT50", "symbol": "NSE:NIFTY NEXT 50", "strike_step": 100},
    {"label": "SENSEX", "symbol": "BSE:SENSEX", "strike_step": 100},
    {"label": "MIDCPNIFTY", "symbol": "NSE:NIFTY MID SELECT", "strike_step": 25},
]

ADMIN_CONFIG_KEYS: tuple[str, ...] = (
    "underlying_symbol",
    "underlying_label",
    "nifty_symbol",
    "nifty_fut_symbol",
    "fut_symbol",
    "ce_symbol",
    "pe_symbol",
    "crude_symbol",
    "india_vix_symbol",
    "strike_step",
    "pcr",
    "max_pain",
    "ivp",
    "dow_change_pct",
    "oi_pct_chg",
    "iv_chg",
    "india_vix",
    "fii_net",
    "entry_ce_premium",
    "entry_pe_premium",
    "exit_pct",
    "mock",
    "engine_enabled",
    "auto_atm_symbols",
)


@dataclass
class SignalEngineConfig:
    mock: bool = False
    engine_enabled: bool = False
    auto_atm_symbols: bool = True
    underlying_symbol: str = ""
    underlying_label: str = ""
    nifty_fut_symbol: str = ""
    ce_symbol: str = ""
    pe_symbol: str = ""
    crude_symbol: str = "MCX:CRUDEOILM"
    dow_change_pct: float | None = None
    strike_step: int = 50
    entry_ce_premium: float = 100
    entry_pe_premium: float = 100
    exit_pct: float = 5
    india_vix_symbol: str = "NSE:INDIA VIX"
    max_pain: float | None = None
    pcr: float | None = None
    ivp: float | None = None
    oi_pct_chg: float | None = None
    iv_chg: float | None = None
    india_vix: float | None = None
    fii_net: float | None = None
    metrics: list[dict[str, Any]] = field(default_factory=lambda: list(DEFAULT_METRICS))

    @classmethod
    def from_settings(cls, settings: dict[str, Any] | None) -> SignalEngineConfig:
        raw = _drop_mismatched_option_symbols(dict(settings or {}))
        metrics = normalize_metrics(list(DEFAULT_METRICS))
        override = raw.get("metrics_json")
        if override:
            try:
                parsed = json.loads(override) if isinstance(override, str) else override
                if isinstance(parsed, list) and parsed:
                    metrics = normalize_metrics(parsed)
            except (TypeError, json.JSONDecodeError):
                pass
        dow = raw.get("dow_change_pct")
        def _opt_float(key: str) -> float | None:
            val = raw.get(key)
            if val is None or val == "":
                return None
            return float(val)

        return cls(
            mock=bool(raw.get("mock", False)),
            engine_enabled=bool(raw.get("engine_enabled", False)),
            auto_atm_symbols=bool(raw.get("auto_atm_symbols", True)),
            underlying_symbol=str(
                raw.get("underlying_symbol") or raw.get("nifty_symbol") or ""
            ).strip(),
            underlying_label=str(raw.get("underlying_label") or "").strip(),
            nifty_fut_symbol=str(
                raw.get("nifty_fut_symbol") or raw.get("fut_symbol") or ""
            ).strip(),
            ce_symbol=_sanitize_option_symbol(raw.get("ce_symbol")),
            pe_symbol=_sanitize_option_symbol(raw.get("pe_symbol")),
            crude_symbol=str(raw.get("crude_symbol") or "MCX:CRUDEOILM"),
            dow_change_pct=float(dow) if dow is not None and dow != "" else None,
            strike_step=int(raw.get("strike_step") or 50),
            entry_ce_premium=float(raw.get("entry_ce_premium") or 100),
            entry_pe_premium=float(raw.get("entry_pe_premium") or 100),
            exit_pct=float(raw.get("exit_pct") or 5),
            india_vix_symbol=str(raw.get("india_vix_symbol") or "NSE:INDIA VIX"),
            max_pain=_opt_float("max_pain"),
            pcr=_opt_float("pcr"),
            ivp=_opt_float("ivp"),
            oi_pct_chg=_opt_float("oi_pct_chg"),
            iv_chg=_opt_float("iv_chg"),
            india_vix=_opt_float("india_vix"),
            fii_net=_opt_float("fii_net"),
            metrics=metrics,
        )

    @property
    def spot_symbol(self) -> str:
        return self.underlying_symbol

    def to_admin_dict(self) -> dict[str, Any]:
        return {
            "underlying_symbol": self.underlying_symbol,
            "underlying_label": self.underlying_label,
            "fut_symbol": self.nifty_fut_symbol,
            "ce_symbol": self.ce_symbol,
            "pe_symbol": self.pe_symbol,
            "crude_symbol": self.crude_symbol,
            "india_vix_symbol": self.india_vix_symbol,
            "strike_step": self.strike_step,
            "pcr": self.pcr,
            "max_pain": self.max_pain,
            "ivp": self.ivp,
            "dow_change_pct": self.dow_change_pct,
            "oi_pct_chg": self.oi_pct_chg,
            "iv_chg": self.iv_chg,
            "india_vix": self.india_vix,
            "fii_net": self.fii_net,
            "entry_ce_premium": self.entry_ce_premium,
            "entry_pe_premium": self.entry_pe_premium,
            "exit_pct": self.exit_pct,
            "mock": self.mock,
            "engine_enabled": self.engine_enabled,
            "auto_atm_symbols": self.auto_atm_symbols,
        }


def _tenant_key(context: TenantContext) -> str:
    return str(context.tenant_id)


def _now_ms() -> float:
    return time.monotonic() * 1000


async def _cache_get(tenant_id: str, metric_id: str) -> Any | None:
    return await cache.get_metric(tenant_id, metric_id)


async def _cache_set(tenant_id: str, metric_id: str, tier: Tier, value: Any) -> None:
    await cache.set_metric(tenant_id, metric_id, tier, value)


def _round_strike(ltp: float, step: int) -> int:
    step = max(step, 1)
    return int(round(ltp / step) * step)


def _compute_adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    n = min(len(highs), len(lows), len(closes))
    if n < period + 1:
        return None
    trs: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for i in range(1, n):
        high, low, close_prev = highs[i], lows[i], closes[i - 1]
        high_prev, low_prev = highs[i - 1], lows[i - 1]
        tr = max(high - low, abs(high - close_prev), abs(low - close_prev))
        up = high - high_prev
        down = low_prev - low
        trs.append(tr)
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / period
    plus = sum(plus_dm[:period]) / period
    minus = sum(minus_dm[:period]) / period
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
        plus = (plus * (period - 1) + plus_dm[i]) / period
        minus = (minus * (period - 1) + minus_dm[i]) / period
    if atr <= 0:
        return 0.0
    plus_di = 100 * plus / atr
    minus_di = 100 * minus / atr
    denom = plus_di + minus_di
    if denom <= 0:
        return 0.0
    dx = 100 * abs(plus_di - minus_di) / denom
    return round(dx, 2)


def _ist_now() -> datetime:
    return datetime.now(IST)


def _ist_session_date() -> str:
    return _ist_now().strftime("%Y-%m-%d")


def _session_dated_key(name: str) -> str:
    return f"{name}:{_ist_session_date()}"


def _option_side_from_symbol(symbol: str) -> str | None:
    """Return CE/PE when ``symbol`` is an option contract (not a FUT/index name).

    Requires a digit before the side suffix so ``NIFTY FIN SERVICE`` (ends in
    ``CE``) is not mistaken for a call option.
    """
    raw = symbol.strip().upper()
    if not raw or raw.endswith("FUT"):
        return None
    body = raw.split(":", 1)[-1]
    match = re.search(r"(\d)(CE|PE)$", body)
    if not match:
        return None
    return match.group(2)


def _sanitize_option_symbol(symbol: str | None) -> str:
    """Drop blanks / FUT / non-option values pasted into CE/PE fields."""
    raw = str(symbol or "").strip()
    if not raw or _option_side_from_symbol(raw) is None:
        return ""
    return raw


def _fut_root(fut_symbol: str) -> str:
    """``BFO:SENSEX26AUGFUT`` → ``SENSEX``; ``NFO:NIFTY26AUGFUT`` → ``NIFTY``.

    Longest-first so ``NIFTYNXT50`` wins over ``NIFTY``. Returns ``""`` when
    the root is not in the known index set.
    """
    body = fut_symbol.strip().upper().split(":", 1)[-1]
    if body.endswith("FUT"):
        body = body[:-3]
    elif body.endswith("CE") or body.endswith("PE"):
        body = body[:-2]
        # Strip trailing strike digits: NIFTY26AUG24500 → NIFTY26AUG
        while body and body[-1].isdigit():
            body = body[:-1]
    known_roots = (
        "MIDCPNIFTY",
        "NIFTYNXT50",
        "BANKNIFTY",
        "FINNIFTY",
        "NIFTY50",
        "NIFTY",
        "SENSEX",
        "BANKEX",
    )
    for root in known_roots:
        if body.startswith(root):
            return root
    return ""


def _option_matches_fut(option_symbol: str, fut_symbol: str) -> bool:
    """True when CE/PE share the FUT option root (blocks NIFTY CE on SENSEX FUT)."""
    opt = _sanitize_option_symbol(option_symbol)
    fut = (fut_symbol or "").strip()
    if not opt or not fut:
        return False
    fut_root = _fut_root(fut)
    opt_root = _fut_root(opt)
    if not fut_root or not opt_root:
        # Unknown roots: do not claim a match (caller decides fail-open vs wipe).
        return False
    return fut_root == opt_root


def _option_strike(symbol: str) -> int | None:
    """Extract strike digits before a trailing CE/PE suffix."""
    raw = symbol.strip().upper().replace(" ", "")
    if ":" in raw:
        raw = raw.split(":", 1)[1]
    if raw.endswith("CE") or raw.endswith("PE"):
        raw = raw[:-2]
    digits = ""
    for ch in reversed(raw):
        if ch.isdigit():
            digits = ch + digits
        else:
            break
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _align_ce_pe_strikes(settings: dict[str, Any]) -> dict[str, Any]:
    """Drop CE/PE when their strikes differ so auto-ATM can refill a matched pair."""
    out = dict(settings)
    ce = str(out.get("ce_symbol") or "").strip()
    pe = str(out.get("pe_symbol") or "").strip()
    if not ce or not pe:
        return out
    ce_strike = _option_strike(ce)
    pe_strike = _option_strike(pe)
    if ce_strike is None or pe_strike is None:
        return out
    if ce_strike != pe_strike:
        out.pop("ce_symbol", None)
        out.pop("pe_symbol", None)
    return out


def _drop_mismatched_option_symbols(settings: dict[str, Any]) -> dict[str, Any]:
    """Clear CE/PE that belong to a different underlying than the configured FUT.

    Unknown FUT roots (hand-typed underlyings not in the known list) fail open —
    leave CE/PE alone rather than silently wiping valid pairs.
    """
    out = dict(settings)
    fut = str(out.get("nifty_fut_symbol") or out.get("fut_symbol") or "").strip()
    if not fut:
        return _align_ce_pe_strikes(out)
    if not _fut_root(fut):
        return _align_ce_pe_strikes(out)
    for key in ("ce_symbol", "pe_symbol"):
        raw = str(out.get(key) or "").strip()
        if raw and not _option_matches_fut(raw, fut):
            out.pop(key, None)
    return _align_ce_pe_strikes(out)


def _signal_settings_patch(
    previous: dict[str, Any], next_settings: dict[str, Any]
) -> dict[str, Any]:
    """Build a Signal-owned settings patch (``None`` = delete).

    Nested desk keys (``options_lab``, ``param_chart``, …) are never included so
    a Signal write cannot wipe or rewrite another desk's subtree.
    """
    owned = set(ADMIN_CONFIG_KEYS) | {"nifty_symbol", "nifty_fut_symbol", "fut_symbol"}
    patch: dict[str, Any] = {}
    for key in owned:
        if key in next_settings:
            if previous.get(key) != next_settings[key]:
                patch[key] = next_settings[key]
        elif key in previous:
            patch[key] = None
    return patch


def _derive_option_symbol(fut_symbol: str, strike: int, side: str) -> str | None:
    """Build `NFO:NIFTY26AUG24500CE` from `NFO:NIFTY26AUGFUT` + ATM strike."""
    side = side.upper()
    if side not in {"CE", "PE"} or strike <= 0:
        return None
    raw = fut_symbol.strip()
    if not raw:
        return None
    if ":" in raw:
        exchange, sym = raw.split(":", 1)
    else:
        exchange, sym = "NFO", raw
    sym = sym.strip().upper()
    if not sym.endswith("FUT"):
        return None
    prefix = sym[:-3]
    if not prefix:
        return None
    return f"{exchange.strip().upper()}:{prefix}{int(strike)}{side}"


def _resolve_option_symbols(config: SignalEngineConfig, atm: int | None) -> tuple[str, str]:
    ce = _sanitize_option_symbol(config.ce_symbol)
    pe = _sanitize_option_symbol(config.pe_symbol)
    if (
        config.auto_atm_symbols
        and atm is not None
        and config.nifty_fut_symbol
    ):
        derived_ce = _derive_option_symbol(config.nifty_fut_symbol, atm, "CE")
        derived_pe = _derive_option_symbol(config.nifty_fut_symbol, atm, "PE")
        if derived_ce and derived_pe:
            return derived_ce, derived_pe
    return ce, pe


def _build_alt_fut_symbol(primary_fut: str, root: str, exchange: str) -> str | None:
    """Reuse expiry suffix from the configured primary FUT (e.g. NIFTY26MAR26 → BANKNIFTY26MAR26)."""
    raw = primary_fut.strip()
    if not root or not raw:
        return None
    if ":" in raw:
        _, sym = raw.split(":", 1)
    else:
        sym = raw
    sym = sym.upper()
    if not sym.endswith("FUT"):
        return None
    body = sym[:-3]
    known_roots = (
        "MIDCPNIFTY",
        "NIFTYNXT50",
        "BANKNIFTY",
        "FINNIFTY",
        "NIFTY50",
        "NIFTY",
        "SENSEX",
    )
    expiry_part: str | None = None
    for known in known_roots:
        if body.startswith(known):
            expiry_part = body[len(known) :]
            break
    if not expiry_part:
        return None
    return f"{exchange.strip().upper()}:{root.strip().upper()}{expiry_part}FUT"


async def _merge_secondary_ce_pe_quotes(
    service: "SignalEngineService",
    config: SignalEngineConfig,
    feed: dict[str, Any],
    quotes: dict[str, Any],
) -> None:
    """Fetch ATM CE/PE for alternate underlyings (SENSEX, BANKNIFTY) on checklist rows."""
    if not config.nifty_fut_symbol:
        return
    pending: dict[str, dict[str, Any]] = {}
    for spec in config.metrics:
        if spec.get("rule") != "ce_pe_balance":
            continue
        underlying = str(spec.get("underlying_symbol") or "").strip()
        if not underlying or underlying == config.underlying_symbol:
            continue
        ce_key = str(spec.get("ce_feed_key") or "").strip()
        pe_key = str(spec.get("pe_feed_key") or "").strip()
        if not ce_key or not pe_key:
            continue
        if underlying in pending:
            continue
        pending[underlying] = {
            "root": str(spec.get("option_root") or "").strip(),
            "exchange": str(spec.get("option_exchange") or "NFO").strip(),
            "strike_step": int(spec.get("strike_step") or 50),
            "ce_key": ce_key,
            "pe_key": pe_key,
        }
    if not pending:
        return

    # Merge secondary fetches into a local overlay only — never mutate the shared
    # quote dict mid-build. Adding keyed rows to the shared dict disables _flat
    # fallback in _find_quote_row and can silently drop FUT OI / PCR.
    overlay: dict[str, Any] = {}

    missing = [
        sym
        for sym in pending
        if _find_keyed_quote_row(quotes, sym) is None and _find_keyed_quote_row(overlay, sym) is None
    ]
    if missing:
        overlay.update(await service._fetch_quote(missing))

    option_symbols: list[str] = []
    resolve_plan: list[tuple[str, str, str, str]] = []
    for underlying, meta in pending.items():
        spot_row = _find_keyed_quote_row(quotes, underlying) or _find_quote_row(overlay, underlying)
        spot = _pick_float(spot_row or {}, "last_price", "ltp", "last")
        if spot is None:
            continue
        atm = _round_strike(spot, int(meta["strike_step"]))
        fut = _build_alt_fut_symbol(
            config.nifty_fut_symbol,
            str(meta["root"]),
            str(meta["exchange"]),
        )
        if not fut:
            continue
        ce_sym = _derive_option_symbol(fut, atm, "CE")
        pe_sym = _derive_option_symbol(fut, atm, "PE")
        if not ce_sym or not pe_sym:
            continue
        option_symbols.extend([ce_sym, pe_sym])
        resolve_plan.append((ce_sym, pe_sym, str(meta["ce_key"]), str(meta["pe_key"])))

    if option_symbols:
        overlay.update(await service._fetch_quote(list(dict.fromkeys(option_symbols))))

    for ce_sym, pe_sym, ce_key, pe_key in resolve_plan:
        ce_row = _find_quote_row(overlay, ce_sym) or _find_keyed_quote_row(quotes, ce_sym)
        pe_row = _find_quote_row(overlay, pe_sym) or _find_keyed_quote_row(quotes, pe_sym)
        ce_val = _pick_float(ce_row or {}, "last_price", "ltp", "last")
        pe_val = _pick_float(pe_row or {}, "last_price", "ltp", "last")
        if ce_val is not None:
            feed[ce_key] = ce_val
        if pe_val is not None:
            feed[pe_key] = pe_val


def _estimate_pcr(ce_row: dict[str, Any] | None, pe_row: dict[str, Any] | None) -> float | None:
    if not ce_row or not pe_row:
        return None
    ce_oi = _pick_float(ce_row, "oi", "open_interest")
    pe_oi = _pick_float(pe_row, "oi", "open_interest")
    if ce_oi is None or pe_oi is None or ce_oi <= 0:
        return None
    return round(pe_oi / ce_oi, 4)


def _merge_option_iv(ce_row: dict[str, Any] | None, pe_row: dict[str, Any] | None) -> float | None:
    values: list[float] = []
    for row in (ce_row, pe_row):
        if not row:
            continue
        iv_val = _pick_float(row, "implied_volatility", "iv")
        if iv_val is not None:
            values.append(iv_val)
    if not values:
        return None
    return sum(values) / len(values)


def _parse_historical_candles(result: Any) -> tuple[list[float], list[float], list[float]]:
    if not isinstance(result, dict) or result.get("ok") is False:
        return [], [], []
    data = result.get("data", result)
    candles: list[Any] = []
    if isinstance(data, dict):
        raw = data.get("candles")
        if isinstance(raw, list):
            candles = raw
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    for row in candles:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            highs.append(float(row[2]))
            lows.append(float(row[3]))
            closes.append(float(row[4]))
        except (TypeError, ValueError):
            continue
    return highs, lows, closes


def _compute_rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss <= 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _quote_change_pcts(row: dict[str, Any] | None) -> tuple[float | None, float | None]:
    if not row:
        return None, None
    ltp = _pick_float(row, "last_price", "ltp", "last")
    ohlc = row.get("ohlc") if isinstance(row.get("ohlc"), dict) else {}
    prev = _pick_float(ohlc, "close")
    open_ = _pick_float(ohlc, "open")
    # Kite / LTP-shaped rows often expose net_change without a nested ohlc block.
    if prev is None and ltp is not None:
        net = _pick_float(row, "net_change", "change", "change_value")
        if net is not None:
            prev = ltp - net
    # Some brokers return day change already as a percent.
    direct_pct = _pick_float(
        row,
        "change_percent",
        "change_pct",
        "percentage_change",
        "pChange",
    )
    vs_prev: float | None = None
    vs_open: float | None = None
    if direct_pct is not None and prev is None:
        vs_prev = round(float(direct_pct), 3)
    elif ltp is not None and prev not in (None, 0):
        vs_prev = round((ltp - prev) / prev * 100, 3)
    if ltp is not None and open_ not in (None, 0):
        vs_open = round((ltp - open_) / open_ * 100, 3)
    return vs_prev, vs_open


def _fut_basis_pct(spot: float | None, fut: float | None) -> float | None:
    if spot is None or fut is None or spot == 0:
        return None
    return round((fut - spot) / spot * 100, 3)


def _apply_quote_pct_map(
    feed: dict[str, Any],
    quotes: dict[str, Any],
    mapping: dict[str, str],
) -> None:
    for feed_key, symbol in mapping.items():
        row = _find_quote_row(quotes, symbol)
        vs_prev, _ = _quote_change_pcts(row)
        if vs_prev is not None:
            feed[feed_key] = vs_prev


def _enrich_derived_feed_fields(feed: dict[str, Any]) -> None:
    ce = feed.get("ce")
    pe = feed.get("pe")
    if ce is not None and pe is not None:
        feed["straddle"] = round(float(ce) + float(pe), 2)
    if feed.get("straddle_decay_pct") is None and feed.get("straddle") is not None:
        open_straddle = feed.get("_straddle_session_open")
        if open_straddle is not None and float(open_straddle) > 0:
            decay = (float(open_straddle) - float(feed["straddle"])) / float(open_straddle) * 100
            feed["straddle_decay_pct"] = round(decay, 3)
    if (
        feed.get("straddle_decay_calm_pct") is None
        and feed.get("straddle_decay_pct") is not None
        and feed.get("macro_events_next_7d") == 0
    ):
        feed["straddle_decay_calm_pct"] = feed["straddle_decay_pct"]
    if feed.get("europe_session_max_abs_chg") is None:
        europe = europe_session_max_abs_chg(feed)
        if europe is not None:
            feed["europe_session_max_abs_chg"] = europe
    if feed.get("gap_pct") is None and feed.get("spot_chg") is not None:
        feed["gap_pct"] = feed["spot_chg"]
    spot = feed.get("nifty_ltp")
    if spot is not None:
        if feed.get("nifty_points_move") is None:
            session_open = feed.get("_session_open_ltp")
            if session_open is not None:
                feed["nifty_points_move"] = round(float(spot) - float(session_open), 2)
    if feed.get("index_sensex_chg") is not None and feed.get("sensex_points_move") is None:
        # points move filled when sensex quote batch includes session open
        pass
    now = _ist_now()
    feed["ist_hour"] = round(now.hour + now.minute / 60.0, 3)
    feed.update(contextual_desk_chart_feeds(feed))


async def _merge_yahoo_slow_tier(
    tenant_id: str,
    feed: dict[str, Any],
    *,
    mock: bool,
) -> None:
    cached = await _cache_get(tenant_id, "yahoo_global")
    if cached is not None:
        feed.update(cached)
        return
    if mock:
        payload = mock_yahoo_changes(GLOBAL_YAHOO_TICKERS)
        crypto = mock_yahoo_changes(CRYPTO_YAHOO_TICKERS)
    else:
        payload = fetch_yahoo_changes(GLOBAL_YAHOO_TICKERS)
        crypto = fetch_yahoo_changes(CRYPTO_YAHOO_TICKERS)
    payload.update(crypto)
    # Compute from the merged payload so BTC (from GLOBAL) is included.
    max_crypto = crypto_max_abs_change(payload)
    if max_crypto is not None:
        payload["global_crypto_max_abs_chg"] = max_crypto
    europe = europe_session_max_abs_chg(payload)
    if europe is not None:
        payload["europe_session_max_abs_chg"] = europe
    if payload:
        feed.update(payload)
        await _cache_set(tenant_id, "yahoo_global", "slow", payload)


async def _merge_yahoo_timing_tier(
    tenant_id: str,
    feed: dict[str, Any],
    *,
    mock: bool,
) -> None:
    """Gold / silver / US·EU futures — session % refreshed ~every minute while trading."""
    cached = await _cache_get(tenant_id, "yahoo_timing")
    if cached is not None:
        feed.update(cached)
        return
    if mock:
        payload = mock_yahoo_changes(TIMING_YAHOO_TICKERS)
    else:
        payload = fetch_yahoo_session_changes(TIMING_YAHOO_TICKERS)
    if payload:
        feed.update(payload)
        await _cache_set(tenant_id, "yahoo_timing", "medium", payload)


def _extract_candle_rows(result: Any) -> list[Any]:
    if not isinstance(result, dict) or result.get("ok") is False:
        return []
    data = result.get("data", result)
    if isinstance(data, dict):
        raw = data.get("candles")
        if isinstance(raw, list):
            return raw
    return []


async def _merge_nse_slow_tier(
    tenant_id: str,
    feed: dict[str, Any],
    config: SignalEngineConfig,
    *,
    mock: bool,
) -> None:
    if mock:
        feed.update(mock_nse_fields())
        return
    cached = await _cache_get(tenant_id, "nse_slow")
    if cached is not None:
        if config.fii_net is None and cached.get("fii_net") is not None:
            feed["fii_net"] = cached["fii_net"]
        if cached.get("advance_decline_ratio") is not None:
            feed["advance_decline_ratio"] = cached["advance_decline_ratio"]
        return
    payload = await asyncio.to_thread(fetch_nse_slow_fields)
    if not payload:
        return
    if config.fii_net is None and payload.get("fii_net") is not None:
        feed["fii_net"] = payload["fii_net"]
    if payload.get("advance_decline_ratio") is not None:
        feed["advance_decline_ratio"] = payload["advance_decline_ratio"]
    await _cache_set(tenant_id, "nse_slow", "slow", payload)


async def _merge_levels_tier(
    service: "SignalEngineService",
    tenant_id: str,
    feed: dict[str, Any],
    *,
    spot_row: dict[str, Any] | None,
    mock: bool,
) -> None:
    merged = False
    try:
        cached = await _cache_get(tenant_id, "levels")
        if cached is not None:
            feed.update(cached)
            merged = True
            return
        if mock:
            spot = float(feed.get("nifty_ltp") or 24312.5)
            payload = mock_levels(spot)
            feed.update(payload)
            await _cache_set(tenant_id, "levels", "medium", payload)
            merged = True
            return
        if spot_row is None:
            return
        token_raw = spot_row.get("instrument_token")
        try:
            token = int(token_raw) if token_raw is not None else 0
        except (TypeError, ValueError):
            token = 0
        if token <= 0:
            return
        now = _ist_now()
        daily_from = (now - timedelta(days=LEVELS_DAILY_HISTORY_DAYS)).strftime("%Y-%m-%d")
        daily_to = now.strftime("%Y-%m-%d")
        session_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        daily_hist = await service._invoke_broker_tool(
            "get_historical_candles",
            {
                "instrument_token": token,
                "interval": "day",
                "from_date": daily_from,
                "to_date": daily_to,
            },
        )
        # Pre-open (before 09:15 IST): skip same-day intraday — Kite 400s when from>to.
        if now > session_open:
            intra_from = session_open.strftime("%Y-%m-%d %H:%M:%S")
            intra_to = now.strftime("%Y-%m-%d %H:%M:%S")
            intra_hist = await service._invoke_broker_tool(
                "get_historical_candles",
                {
                    "instrument_token": token,
                    "interval": "5minute",
                    "from_date": intra_from,
                    "to_date": intra_to,
                },
            )
            minute_hist = await service._invoke_broker_tool(
                "get_historical_candles",
                {
                    "instrument_token": token,
                    "interval": "minute",
                    "from_date": intra_from,
                    "to_date": intra_to,
                },
            )
        else:
            intra_hist = {}
            minute_hist = {}
        hour_from = (now - timedelta(days=5)).strftime("%Y-%m-%d")
        hour_hist = await service._invoke_broker_tool(
            "get_historical_candles",
            {
                "instrument_token": token,
                "interval": "60minute",
                "from_date": hour_from,
                "to_date": daily_to,
            },
        )
        daily_rows = _extract_candle_rows(daily_hist)
        intra_rows = _extract_candle_rows(intra_hist)
        minute_rows = _extract_candle_rows(minute_hist)
        hour_rows = _extract_candle_rows(hour_hist)
        payload = levels_from_candles(
            daily_candles=daily_rows,
            intraday_5m=intra_rows,
            spot=feed.get("nifty_ltp"),
        )
        payload.update(expiry_levels_from_daily(daily_rows, ref=now.date()))
        payload.update(
            intraday_indicators_from_candles(minute_rows, feed.get("nifty_ltp"))
        )
        payload.update(
            chart_timeframe_snapshots(
                minute_candles=minute_rows,
                five_min_candles=intra_rows,
                hour_candles=hour_rows,
                daily_candles=daily_rows,
            )
        )
        if not payload:
            return
        feed.update(payload)
        await _cache_set(tenant_id, "levels", "medium", payload)
        merged = True
    finally:
        if merged:
            _refresh_level_spot_fields(feed, spot_row)


def _refresh_level_spot_fields(
    feed: dict[str, Any],
    spot_row: dict[str, Any] | None,
) -> None:
    """Live day range + spot comparisons — must run each feed tick."""
    if spot_row:
        ohlc = spot_row.get("ohlc") if isinstance(spot_row.get("ohlc"), dict) else {}
        day_high = _pick_float(ohlc, "high")
        day_low = _pick_float(ohlc, "low")
        if day_high is not None:
            feed["day_high"] = day_high
        if day_low is not None:
            feed["day_low"] = day_low
    spot = feed.get("nifty_ltp")
    if spot is not None and feed.get("pivot_point") is not None:
        apply_spot_derived_fields(feed, float(spot))


async def _apply_straddle_decay(tenant_id: str, feed: dict[str, Any]) -> None:
    straddle = feed.get("straddle")
    if straddle is None:
        return
    session_key = _session_dated_key("straddle_session_open")
    session_open = await cache.get_session_value(tenant_id, session_key)
    if session_open is None:
        await cache.set_session_value(tenant_id, session_key, float(straddle))
        feed["_straddle_session_open"] = float(straddle)
        return
    feed["_straddle_session_open"] = float(session_open)
    if float(session_open) > 0:
        decay = (float(session_open) - float(straddle)) / float(session_open) * 100
        feed["straddle_decay_pct"] = round(decay, 3)


def _merge_chain_payload(feed: dict[str, Any], payload: dict[str, Any], config: SignalEngineConfig) -> None:
    if config.pcr is None and payload.get("pcr") is not None and feed.get("pcr") is None:
        feed["pcr"] = payload["pcr"]
        feed["pcr_source"] = "chain_oi"
    if config.max_pain is None and payload.get("max_pain") is not None and feed.get("max_pain") is None:
        feed["max_pain"] = payload["max_pain"]
    if payload.get("writer_grip_score") is not None:
        feed["writer_grip_score"] = payload["writer_grip_score"]


async def _merge_option_chain_tier(
    service: "SignalEngineService",
    tenant_id: str,
    feed: dict[str, Any],
    config: SignalEngineConfig,
    *,
    atm_strike: int | None,
    mock: bool,
) -> None:
    if atm_strike is None or not config.nifty_fut_symbol:
        return
    need_pcr = config.pcr is None and feed.get("pcr") is None
    need_max_pain = config.max_pain is None and feed.get("max_pain") is None
    need_writer = feed.get("writer_grip_score") is None
    if not need_pcr and not need_max_pain and not need_writer:
        return
    cached = await _cache_get(tenant_id, "option_chain")
    if isinstance(cached, dict):
        _merge_chain_payload(feed, cached, config)
        if (not need_pcr or feed.get("pcr") is not None) and (
            not need_max_pain or feed.get("max_pain") is not None
        ):
            return
    if mock:
        payload = {
            "pcr": 1.25,
            "max_pain": float(atm_strike + 100),
            "writer_grip_score": 0.28,
        }
        _merge_chain_payload(feed, payload, config)
        await _cache_set(tenant_id, "option_chain", "medium", payload)
        return
    strikes, ce_syms, pe_syms = build_chain_symbols(
        config.nifty_fut_symbol,
        atm_strike,
        config.strike_step,
        wings=5,
    )
    if not ce_syms or not pe_syms:
        return
    chain_quotes = await service._fetch_quote(ce_syms + pe_syms, prefer="get_quote")
    payload = chain_metrics_from_quotes(
        chain_quotes,
        find_row=_find_quote_row,
        strikes=strikes,
        ce_symbols=ce_syms,
        pe_symbols=pe_syms,
    )
    if not payload:
        return
    _merge_chain_payload(feed, payload, config)
    await _cache_set(tenant_id, "option_chain", "medium", payload)


def _oi_baseline_cache_key() -> str:
    return f"oi_baseline:{_ist_session_date()}"


def _mock_feed(config: SignalEngineConfig) -> dict[str, Any]:
    """Demo values aligned with the ops spreadsheet rehearsal."""
    atm = 24300
    ce = 125.0
    pe = 55.0
    iv_current = -11.0
    iv_high = 22.0 if iv_current > 0 else abs(iv_current) * 2
    return {
        "nifty_ltp": 24312.5,
        "atm": atm,
        "ce": ce,
        "pe": pe,
        "oi": 50.0,
        "adx": 45.0,
        "iv": iv_current,
        "iv_day_high": iv_high,
        "iv_chg": -0.3,
        "crude_ltp": 87.0,
        "crude_prev_close": 88.5,
        "dow_change_pct": -0.50,
        "pcr": 1.25,
        "ivp": 45.0,
        "india_vix": 14.2,
        "max_pain": 24400.0,
        "oi_pct_chg": 0.4,
        "spot_chg": 0.25,
        "spot_vs_open": 0.15,
        "fut_basis": 0.12,
        "ce_oi": 1_250_000.0,
        "pe_oi": 1_560_000.0,
        "vix_chg": -0.4,
        "rsi": 52.0,
        "straddle": 180.0,
        "_straddle_session_open": 185.0,
        "straddle_decay_pct": 2.7,
        "straddle_decay_calm_pct": 2.7,
        "writer_grip_score": 0.31,
        "global_bond_proxy_chg": -0.15,
        "europe_session_max_abs_chg": 0.22,
        "atm_volume": 125000.0,
        "sensex_ce": 118.0,
        "sensex_pe": 118.4,
        "banknifty_ce": 142.0,
        "banknifty_pe": 141.6,
        "global_crypto_max_abs_chg": 1.85,
        "usd_inr": 83.12,
        "gap_pct": 0.25,
        "index_nifty_chg": 0.25,
        "index_sensex_chg": 0.22,
        "index_banknifty_chg": 0.18,
        "index_finnifty_chg": 0.15,
        "nifty_points_move": 35.0,
        "sensex_points_move": 120.0,
        "ist_hour": 9.5,
        **mock_yahoo_changes(GLOBAL_YAHOO_TICKERS),
        **mock_yahoo_changes(TIMING_YAHOO_TICKERS),
        **mock_levels(24312.5),
        **mock_nse_fields(),
        "source": "mock",
    }


def _mock_feed_live(config: SignalEngineConfig) -> dict[str, Any]:
    """Mock feed with time-varying fast-tier fields for stream rehearsal."""
    feed = _mock_feed(config)
    t = time.time()
    wobble = math.sin(t / 4.0) * 8.0
    spot = 24312.5 + wobble
    feed["nifty_ltp"] = round(spot, 2)
    feed["atm"] = _round_strike(spot, config.strike_step)
    feed["ce"] = round(125.0 + wobble * 0.15, 2)
    feed["pe"] = round(55.0 - wobble * 0.08, 2)
    feed["india_vix"] = round(14.2 + math.sin(t / 7.0) * 0.3, 2)
    _enrich_derived_feed_fields(feed)
    apply_spot_derived_fields(feed, spot)
    return feed


def _normalize_quote_payload(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    if result.get("ok") is False:
        return {}
    data = result.get("data", result)
    if not isinstance(data, dict):
        return {}
    # Single-instrument full quote (Groww get_quote) — check before quote-map heuristic.
    if any(key in data for key in ("last_price", "open_interest", "implied_volatility")):
        ts = data.get("trading_symbol") or data.get("symbol")
        if ts:
            return {str(ts): data}
        return {"_flat": data}
    if _looks_like_quote_map(data):
        normalized: dict[str, Any] = {}
        for row in _quote_map_rows(data):
            sym = row.get("symbol")
            if sym:
                normalized[str(sym)] = row
        return normalized
    return data


def _quote_keys_match(
    quote_key: str,
    *,
    norm: str,
    groww: str,
    canon_norm: str | None = None,
) -> bool:
    qk = quote_key.upper().replace(" ", "")
    qk_canon = resolve_kite_instrument(quote_key).upper().replace(" ", "")
    if qk == norm or qk_canon == norm or qk == groww:
        return True
    if canon_norm and (qk == canon_norm or qk_canon == canon_norm):
        return True
    if "_" in qk and qk.split("_", 1)[-1] == groww:
        return True
    return False


def _quote_row_from_value(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, (int, float)):
        return {"ltp": value, "last_price": value}
    return None


def _find_quote_row(quotes: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    if not quotes or not symbol:
        return None
    key = symbol.strip()
    resolved = resolve_kite_instrument(key)
    norm = key.upper().replace(" ", "")
    canon_norm = resolved.upper().replace(" ", "")
    groww = _groww_symbol(key).upper()
    for candidate in dict.fromkeys([key, resolved]):
        direct = _quote_row_from_value(quotes.get(candidate))
        if direct is not None:
            return direct
    keyed = {qk: row for qk, row in quotes.items() if qk != "_flat"}
    for quote_key, row in keyed.items():
        if not _quote_keys_match(
            str(quote_key),
            norm=norm,
            groww=groww,
            canon_norm=canon_norm,
        ):
            continue
        parsed = _quote_row_from_value(row)
        if parsed is not None:
            return parsed
    if not keyed and "_flat" in quotes:
        return _quote_row_from_value(quotes["_flat"])
    return None


def _find_keyed_quote_row(quotes: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    """Keyed quote lookup only — never uses the shared ``_flat`` fallback."""
    if not quotes or not symbol:
        return None
    key = symbol.strip()
    resolved = resolve_kite_instrument(key)
    norm = key.upper().replace(" ", "")
    canon_norm = resolved.upper().replace(" ", "")
    groww = _groww_symbol(key).upper()
    for candidate in dict.fromkeys([key, resolved]):
        direct = _quote_row_from_value(quotes.get(candidate))
        if direct is not None:
            return direct
    for quote_key, row in quotes.items():
        if quote_key == "_flat":
            continue
        if not _quote_keys_match(
            str(quote_key),
            norm=norm,
            groww=groww,
            canon_norm=canon_norm,
        ):
            continue
        parsed = _quote_row_from_value(row)
        if parsed is not None:
            return parsed
    return None


def _broker_auth_warning(quote_error: str | None) -> str | None:
    """Map swallowed sandbox auth failures to an actionable desk warning."""
    text = (quote_error or "").lower()
    if not text:
        return None
    if "api_key" in text or "access_token" in text or "credential" in text:
        return (
            "Kite credentials missing on kite-toolkit — attach a tenant credential "
            "JSON with api_key + access_token (daily token) and publish."
        )
    if "token" in text and ("invalid" in text or "expired" in text or "forbidden" in text):
        return (
            "Kite access_token rejected — mint a fresh token (~expires 06:00 IST) "
            "and update the kite-toolkit credential."
        )
    return None


def _live_setup_warnings(
    config: SignalEngineConfig,
    feed: dict[str, Any],
    *,
    has_broker: bool,
    team_ready: bool,
    quote_error: str | None = None,
) -> list[str]:
    if config.mock:
        return []
    warnings: list[str] = []
    if not team_ready:
        warnings.append("Publish the Signals ops team and bind tools.")
    if not has_broker:
        warnings.append("Bind Kite (recommended) or Groww read-only quotes on Signals ops.")
    auth_hint = _broker_auth_warning(quote_error)
    if auth_hint:
        warnings.append(auth_hint)
    if not config.underlying_symbol:
        warnings.append("Select an underlying symbol (Admin → Signal config).")
    elif feed.get("nifty_ltp") is None and not auth_hint:
        warnings.append(
            f"No live print for {config.underlying_symbol}. Check broker token and symbol."
        )
    ce_ok = bool(_sanitize_option_symbol(config.ce_symbol))
    pe_ok = bool(_sanitize_option_symbol(config.pe_symbol))
    can_auto_atm = bool(
        config.auto_atm_symbols and config.nifty_fut_symbol and feed.get("atm") is not None
    )
    if not ce_ok or not pe_ok:
        if not (can_auto_atm or (config.auto_atm_symbols and config.nifty_fut_symbol)):
            warnings.append(
                "Set CE/PE option symbols (…CE / …PE), not FUT — or set FUT and enable auto ATM."
            )
        elif feed.get("ce") is None or feed.get("pe") is None:
            if feed.get("nifty_ltp") is not None:
                warnings.append(
                    "CE/PE quotes missing — check FUT expiry/ATM and that Kite returns option LTPs."
                )
    elif feed.get("ce") is None or feed.get("pe") is None:
        warnings.append("CE/PE quotes missing — check option symbols and market hours.")
    if not config.nifty_fut_symbol:
        warnings.append("Set nifty_fut_symbol for live OI (e.g. NFO:NIFTY26AUGFUT).")
    elif feed.get("oi") is None:
        warnings.append(
            "OI not returned — set a valid FUT symbol (e.g. NFO:NIFTY26AUGFUT) "
            "and ensure get_quote returns open_interest on the FNO segment."
        )
    if config.pcr is None and feed.get("pcr") is None:
        warnings.append(
            "PCR missing — set manually or ensure multi-strike OI quotes return open_interest."
        )
    if config.max_pain is None and feed.get("max_pain") is None:
        warnings.append(
            "Max pain missing — set manually or ensure FUT + ATM ±5 strike OI quotes are available."
        )
    if config.ivp is None:
        warnings.append("Set ivp in tool settings until IV history feed is wired.")
    if config.dow_change_pct is None:
        warnings.append("Set dow_change_pct once per session (slow tier).")
    if feed.get("india_vix") is None:
        warnings.append(f"No India VIX print for {config.india_vix_symbol}.")
    if feed.get("adx") is None:
        warnings.append(
            "ADX unavailable — underlying get_quote must return instrument_token "
            "and Kite get_historical_candles must be bound."
        )
    return warnings


def _pick_float(payload: Any, *keys: str) -> float | None:
    if not isinstance(payload, dict):
        return None
    for key in keys:
        val = payload.get(key)
        if val is None or val == "":
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    ohlc = payload.get("ohlc")
    if isinstance(ohlc, dict):
        for key in ("close", "open", "high", "low"):
            val = ohlc.get(key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    continue
    return None


def _evaluate_rule(
    rule: Rule,
    value: float | None,
    target: float,
    *,
    feed: dict[str, Any],
    ce: float | None,
    pe: float | None,
    spec: dict[str, Any] | None = None,
) -> bool | None:
    spec = spec or {}
    if rule == "info":
        return None  # display-only; never gates BUY or shows Pass/Fail
    if rule == "spot_below_max_pain":
        spot = feed.get("nifty_ltp") or feed.get("atm")
        max_pain = feed.get("max_pain")
        if spot is None or max_pain is None:
            return None
        return float(spot) < float(max_pain)
    if value is None and rule not in {
        "ce_pe_balance",
        "below_prev_close",
        "iv_pct_day_high",
        "between",
        "spot_below_max_pain",
    }:
        return None
    if rule == "lt":
        return value is not None and value < target
    if rule == "gt":
        return value is not None and value > target
    if rule == "lte":
        return value is not None and value <= target
    if rule == "gte":
        return value is not None and value >= target
    if rule == "eq":
        return value is not None and math.isclose(value, target, rel_tol=1e-4, abs_tol=0.01)
    if rule == "abs_lte":
        return value is not None and abs(value) <= target
    if rule == "between":
        if value is None:
            return None
        high = float(spec.get("target_high", target))
        low = float(target)
        return low <= float(value) <= high
    if rule == "below_prev_close":
        ltp = feed.get("crude_ltp")
        prev = feed.get("crude_prev_close")
        if ltp is None or prev is None:
            return None
        return float(ltp) < float(prev)
    if rule == "ce_pe_balance":
        if ce is None or pe is None:
            return None
        return math.isclose(ce, pe, rel_tol=0, abs_tol=0.5)
    if rule == "iv_pct_day_high":
        iv = feed.get("iv")
        high = feed.get("iv_day_high")
        if iv is None or high is None or float(high) == 0:
            return None
        pct = (float(iv) / float(high)) * 100
        return pct <= target
    if rule == "before_time":
        current = feed.get("ist_hour")
        if current is None:
            now = _ist_now()
            current = now.hour + now.minute / 60.0
        return float(current) < target
    return None


def _format_target(rule: Rule, target: float, spec: dict[str, Any] | None = None) -> str:
    spec = spec or {}
    if rule == "lt":
        return f"< {target:g}"
    if rule == "gt":
        return f"> {target:g}"
    if rule == "lte":
        return f"≤ {target:g}"
    if rule == "gte":
        return f"≥ {target:g}"
    if rule == "eq":
        return f"= {target:g}"
    if rule == "abs_lte":
        return f"within ±{target:g}%"
    if rule == "between":
        high = spec.get("target_high", target)
        return f"{target:g} – {float(high):g}"
    if rule == "below_prev_close":
        return "below y'day close"
    if rule == "ce_pe_balance":
        return "CE = PE"
    if rule == "iv_pct_day_high":
        return f"{target:g}% of day high"
    if rule == "spot_below_max_pain":
        return "spot < max pain"
    if rule == "before_time":
        return f"before {int(target):g}:00 IST"
    if rule == "info":
        return "live strike"
    return str(target)


def _metric_value(
    metric_id: str,
    feed: dict[str, Any],
    spec: dict[str, Any] | None = None,
) -> float | None:
    spec = spec or {}
    feed_key = spec.get("feed_key")
    if feed_key:
        val = feed.get(feed_key)
        if val is None and feed_key == "ist_hour":
            now = _ist_now()
            return round(now.hour + now.minute / 60.0, 3)
        return float(val) if val is not None else None
    mapping = {
        "adx": "adx",
        "oi": "oi",
        "iv": "iv",
        "iv_chg": "iv_chg",
        "crude_oil": "crude_ltp",
        "dow_jones": "dow_change_pct",
        "atm": "atm",
        "ce": "ce",
        "pe": "pe",
        "pcr": "pcr",
        "ivp": "ivp",
        "india_vix": "india_vix",
        "max_pain": "max_pain",
        "oi_pct_chg": "oi_pct_chg",
        "spot_chg": "spot_chg",
        "spot_vs_open": "spot_vs_open",
        "fut_basis": "fut_basis",
        "ce_oi": "ce_oi",
        "pe_oi": "pe_oi",
        "rsi": "rsi",
        "vix_chg": "vix_chg",
        "fii_net": "fii_net",
    }
    key = mapping.get(metric_id)
    if not key:
        return None
    val = feed.get(key)
    return float(val) if val is not None else None


class SignalEngineService:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context
        self.teams = TeamRepository(session, context)
        self.tools = ToolDefinitionRepository(session, context)
        self.tool_versions = ToolDefinitionVersionRepository(session, context)
        self._last_quote_error: str | None = None

    @staticmethod
    def _config_blob_settings(definition: Any) -> dict[str, Any]:
        return dict((getattr(definition, "config", None) or {}).get("settings") or {})

    async def _tool_settings(self, definition: Any) -> dict[str, Any]:
        """Prefer published version settings; fall back to definition.config.

        Start/Stop must not silently diverge when only one store was updated.
        """
        published_id = getattr(definition, "published_version_id", None)
        if published_id is not None:
            row = await self.tool_versions.get(published_id)
            if row is not None and isinstance(row.settings, dict) and row.settings:
                return dict(row.settings)
        return self._config_blob_settings(definition)

    async def _collect_settings(self) -> dict[str, Any]:
        settings: dict[str, Any] = {}
        async for _team, _version, binding, _source in self._iter_signal_bindings():
            if binding.tool_definition_id is None:
                continue
            definition = await self.tools.get(binding.tool_definition_id)
            if not definition:
                continue
            slug = (definition.slug or "").lower()
            if definition.kind == "tenant_python" and "signal" in slug:
                settings.update(await self._tool_settings(definition))
            elif definition.kind == "tenant_python" and "signal" not in slug:
                # Broker toolkit — skip merging quote tool settings into signal config.
                continue
            else:
                settings.update(await self._tool_settings(definition))
        return settings

    async def _signal_engine_tool(self) -> Any | None:
        async for _team, _version, binding, _source in self._iter_signal_bindings():
            if binding.tool_definition_id is None:
                continue
            definition = await self.tools.get(binding.tool_definition_id)
            if not definition or definition.kind != "tenant_python":
                continue
            if "signal" in (definition.slug or "").lower():
                return definition
        return None

    async def _load_config(self) -> SignalEngineConfig:
        return SignalEngineConfig.from_settings(await self._collect_settings())

    async def _has_quote_binding(self) -> bool:
        """True when a non-signal tenant_python toolkit is bound (likely broker quotes)."""
        async for _team, _version, binding, _source in self._iter_signal_bindings():
            if binding.tool_definition_id is None:
                continue
            definition = await self.tools.get(binding.tool_definition_id)
            if definition is None or definition.kind != "tenant_python":
                continue
            if "signal" not in (definition.slug or "").lower():
                return True
        return False

    async def _load_setup(
        self,
    ) -> tuple[SignalEngineConfig, bool, bool]:
        tenant_id = _tenant_key(self.context)
        hit = await _cache_get(tenant_id, "setup")
        if hit is not None:
            return (
                SignalEngineConfig.from_settings(hit["settings"]),
                bool(hit["has_broker"]),
                bool(hit["team_ready"]),
            )
        settings = await self._collect_settings()
        has_broker = await self._has_quote_binding()
        team_ready = await self._team_ready()
        await _cache_set(
            tenant_id,
            "setup",
            "medium",
            {
                "settings": settings,
                "has_broker": has_broker,
                "team_ready": team_ready,
            },
        )
        return SignalEngineConfig.from_settings(settings), has_broker, team_ready

    async def get_admin_config(self) -> dict[str, Any]:
        config = await self._load_config()
        tool = await self._signal_engine_tool()
        # Match Options Lab: index presets + equity F&O list so screener picks
        # (e.g. ASIANPAINT) show as named PRESET values, not always Custom….
        from app.domains.options_lab import OptionsLabService
        from app.domains.options_lab_underlyings import merge_presets

        equity_presets: list[dict[str, Any]] = []
        equity_meta: dict[str, Any] = {"source": "none"}
        try:
            lab = OptionsLabService(self.session, self.context)
            equity_presets, equity_meta = await lab._equity_presets()
        except Exception:  # noqa: BLE001
            equity_presets = []
        presets = merge_presets(
            [{**p, "universe": "indices"} for p in UNDERLYING_PRESETS],
            equity_presets,
        )
        return {
            "config": config.to_admin_dict(),
            "presets": presets,
            "equity_source": equity_meta.get("source"),
            "equity_count": len(equity_presets),
            "tool_bound": tool is not None,
            "tool_slug": tool.slug if tool else None,
        }

    async def update_admin_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        tool = await self._signal_engine_tool()
        if tool is None:
            return {"ok": False, "error": "Signal engine tool not bound on Signals ops team."}
        current = await self._tool_settings(tool)
        merged = {**current}
        for key in ADMIN_CONFIG_KEYS:
            if key not in patch:
                continue
            val = patch[key]
            if val is None or val == "":
                merged.pop(key, None)
            else:
                merged[key] = val
        # Switching underlying/FUT without new CE/PE must not keep foreign options
        # (e.g. NFO:NIFTY…CE while FUT is BFO:SENSEX…).
        under_changed = bool(
            patch.get("underlying_symbol")
            or patch.get("fut_symbol")
            or patch.get("nifty_fut_symbol")
        )
        if under_changed and "ce_symbol" not in patch and "pe_symbol" not in patch:
            merged.pop("ce_symbol", None)
            merged.pop("pe_symbol", None)
        # CE/PE must be option contracts (…CE / …PE), never a FUT paste.
        for opt_key in ("ce_symbol", "pe_symbol"):
            if opt_key in merged:
                cleaned = _sanitize_option_symbol(str(merged.get(opt_key) or ""))
                if cleaned:
                    merged[opt_key] = cleaned
                else:
                    merged.pop(opt_key, None)
        # Keep legacy alias in sync for older readers.
        if merged.get("underlying_symbol"):
            merged["nifty_symbol"] = merged["underlying_symbol"]
        if merged.get("fut_symbol"):
            merged["nifty_fut_symbol"] = merged["fut_symbol"]
        elif merged.get("nifty_fut_symbol") and not merged.get("fut_symbol"):
            merged["fut_symbol"] = merged["nifty_fut_symbol"]
        merged = _drop_mismatched_option_symbols(merged)
        # Patch only Signal-owned keys so Options Lab / Param Chart nested
        # subtrees (and concurrent Start/Stop) are not clobbered by a stale blob.
        signal_patch = _signal_settings_patch(current, merged)
        await self._patch_tool_settings(tool, signal_patch)
        # Expire identity so subsequent reads in this request see the write.
        await self.session.flush()
        await self.session.refresh(tool)
        tenant_id = _tenant_key(self.context)
        if patch.get("engine_enabled") is False:
            await cache.clear_watcher(tenant_id)
        await _invalidate_tenant_signal_cache(tenant_id)
        # Re-seed boolean after invalidate so SSE fast path does not cold-load
        # for up to medium TTL after Start/Stop.
        await cache.set_metric(
            tenant_id,
            "engine_enabled",
            "medium",
            bool(SignalEngineConfig.from_settings(merged).engine_enabled),
        )
        if patch.get("engine_enabled") is False:
            stopped = _apply_engine_stopped_overlay(
                await self.state()
            )
            await cache.set_snapshot(tenant_id, stopped, force=True)
        elif patch.get("engine_enabled") is True or under_changed:
            # Start desk watching immediately. Snapshot warm is scheduled from the
            # API via BackgroundTasks after the request transaction commits — a
            # create_task here can race and cache a stopped snapshot.
            await cache.touch_watcher(tenant_id)
            # Paint immediately after Start or underlying/FUT change so SSE does
            # not sit on an empty Redis key while the cold tick runs.
            starting = _engine_starting_payload(SignalEngineConfig.from_settings(merged))
            await cache.set_snapshot(
                tenant_id,
                starting,
                ttl_ms=ENGINE_STARTING_SNAPSHOT_MS,
                force=True,
            )
        return {"ok": True, **await self.get_admin_config()}

    async def maybe_persist_auto_atm_symbols(self, payload: dict[str, Any]) -> bool:
        """Write derived ATM CE/PE into tool settings when auto-ATM filled empty/mismatched slots.

        Keeps ticker sync + UI fields aligned with what the feed is quoting (esp. SENSEX).
        """
        config = await self._load_config()
        if not config.auto_atm_symbols or not config.nifty_fut_symbol:
            return False
        if not isinstance(payload, dict):
            return False
        feed = payload.get("feed") if isinstance(payload.get("feed"), dict) else {}
        ce = _sanitize_option_symbol(
            str(payload.get("ce_symbol") or feed.get("ce_symbol") or "")
        )
        pe = _sanitize_option_symbol(
            str(payload.get("pe_symbol") or feed.get("pe_symbol") or "")
        )
        if not ce or not pe:
            return False
        if not _option_matches_fut(ce, config.nifty_fut_symbol):
            return False
        if not _option_matches_fut(pe, config.nifty_fut_symbol):
            return False
        if ce == config.ce_symbol and pe == config.pe_symbol:
            return False
        # Throttle DB writes when ATM drifts during the session.
        tenant_id = _tenant_key(self.context)
        filling_empty = not config.ce_symbol or not config.pe_symbol
        if not filling_empty:
            if await cache.get_metric(tenant_id, "auto_atm_persist_gate") is not None:
                return False
        tool = await self._signal_engine_tool()
        if tool is None:
            return False
        fut = config.nifty_fut_symbol
        atm_patch = {
            "ce_symbol": ce,
            "pe_symbol": pe,
            "fut_symbol": fut,
            "nifty_fut_symbol": fut,
        }
        atm_patch = _drop_mismatched_option_symbols(atm_patch)
        if (
            str(atm_patch.get("ce_symbol") or "") == config.ce_symbol
            and str(atm_patch.get("pe_symbol") or "") == config.pe_symbol
        ):
            return False
        # Only CE/PE (+ FUT aliases) — leave nested desk settings alone.
        write_patch: dict[str, Any] = {
            "ce_symbol": atm_patch.get("ce_symbol") or None,
            "pe_symbol": atm_patch.get("pe_symbol") or None,
            "fut_symbol": atm_patch.get("fut_symbol") or fut,
            "nifty_fut_symbol": atm_patch.get("nifty_fut_symbol") or fut,
        }
        await self._patch_tool_settings(tool, write_patch)
        if not filling_empty:
            await cache.set_metric(tenant_id, "auto_atm_persist_gate", "medium", True)
        logger.info(
            "signal_auto_atm_persisted",
            tenant_id=tenant_id,
            ce=ce,
            pe=pe,
        )
        return True

    async def _patch_tool_settings(
        self, definition: Any, patch: dict[str, Any]
    ) -> dict[str, Any]:
        """Shallow-merge ``patch`` into the latest tool settings and persist.

        ``None`` values delete keys. Nested dicts replace that key wholesale
        (Options Lab / Param Chart own their subtrees). Unmentioned top-level
        keys are preserved so concurrent desk writers cannot clobber each other
        with a stale whole-blob read-modify-write.

        Locks the tool definition + published/draft version rows for the
        read-merge-write so overlapping Signal / Options Lab / Param Chart
        patches on different keys cannot lose each other's updates.
        """
        locked = await self.tools.get(definition.id, lock=True)
        if locked is None:
            raise LookupError("signal engine tool definition not found")
        published_id = getattr(locked, "published_version_id", None)
        if published_id is not None:
            await self.tool_versions.get(published_id, lock=True)
        await self.tool_versions.latest_draft(locked.id, lock=True)
        current = await self._tool_settings(locked)
        merged = dict(current)
        for key, val in patch.items():
            if val is None:
                merged.pop(key, None)
            else:
                merged[key] = val
        await self._write_tool_settings(locked, merged)
        return merged

    async def _write_tool_settings(self, definition: Any, settings: dict[str, Any]) -> None:
        config = dict(definition.config or {})
        config["settings"] = settings
        definition.config = config
        if definition.published_version_id:
            row = await self.tool_versions.get(definition.published_version_id)
            if row is not None:
                row.settings = settings
        draft = await self.tool_versions.latest_draft(definition.id)
        if draft is not None and draft.status == "draft":
            draft.settings = settings
        await self.session.flush()

    async def _team_ready(self) -> bool:
        config = await self.teams.get_config_by_slug(SIGNAL_TEAM_SLUG)
        return bool(config and config.published_version_id)

    async def _iter_signal_bindings(self):
        config = await self.teams.get_config_by_slug(SIGNAL_TEAM_SLUG)
        if config is None or config.published_version_id is None:
            return
        version = await self.teams.get_version(config.published_version_id)
        if version is None:
            return
        bindings = await self.teams.bindings(version.id)
        for binding in bindings:
            yield config, version, binding, None

    async def _broker_tools(self) -> list[Any]:
        factory = AgentFactoryService(self.session, self.context)
        fns: list[Any] = []
        async for _team, _version, binding, _source in self._iter_signal_bindings():
            if binding.tool_definition_id is None:
                continue
            try:
                built = await factory._build_tool(binding)
            except McpToolSkipped:
                continue
            except Exception as exc:
                logger.warning(
                    "signal_broker_tool_build_failed",
                    error=str(exc)[:240],
                    tool_id=str(binding.tool_definition_id),
                )
                continue
            callables = built if isinstance(built, list) else [built]
            for fn in callables:
                name = getattr(fn, "__name__", "")
                if name in SIGNAL_BROKER_CAPABILITIES:
                    fns.append(fn)
        return fns

    async def _invoke_broker_tool(
        self,
        name: str,
        kwargs: dict[str, Any],
        *,
        timeout_s: float = 15.0,
    ) -> Any | None:
        for fn in await self._broker_tools():
            if getattr(fn, "__name__", "") != name:
                continue
            try:
                return await asyncio.wait_for(
                    asyncio.shield(invoke_tool(fn, kwargs)),
                    timeout=timeout_s,
                )
            except TimeoutError:
                logger.warning(
                    "signal_broker_tool_invoke_timeout",
                    tool=name,
                    timeout_s=timeout_s,
                )
                return None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "signal_broker_tool_invoke_failed",
                    tool=name,
                    error=str(exc)[:240],
                )
                return None
        return None

    async def _quote_tools(self) -> list[Any]:
        return [
            fn
            for fn in await self._broker_tools()
            if getattr(fn, "__name__", "") in QUOTE_CAPABILITIES
        ]

    async def _fetch_quote(
        self,
        symbols: list[str],
        *,
        prefer: str | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        symbols = [s for s in symbols if s]
        if not symbols:
            return {}
        cached_key = f"quote:{prefer or 'auto'}:{','.join(symbols)}"
        tenant_id = _tenant_key(self.context)
        hit = await _cache_get(tenant_id, cached_key)
        if hit is not None:
            self._last_quote_error = None
            return hit

        # Per-symbol book (ticker + REST-seeded). Screener batches write here so
        # Options Lab / Signal single-symbol lookups do not re-hit the sandbox.
        book_partial: dict[str, Any] = {}
        ticker_live: dict[str, Any] = {}
        write_rest_quote_book = None
        overlay_ticker_rows = None
        try:
            from app.domains.kite_ticker_hub import (
                assemble_quotes_from_book,
                overlay_ticker_rows as _overlay_ticker_rows,
                write_rest_quote_book as _write_rest_quote_book,
            )

            overlay_ticker_rows = _overlay_ticker_rows
            write_rest_quote_book = _write_rest_quote_book
            book_partial = (
                await assemble_quotes_from_book(
                    tenant_id,
                    symbols,
                    require_all=False,
                    require_alive=False,
                )
                or {}
            )
            ticker_live = (
                await assemble_quotes_from_book(
                    tenant_id,
                    symbols,
                    require_all=False,
                    require_alive=True,
                )
                or {}
            )
        except Exception:
            book_partial = {}
            ticker_live = {}

        def _row_reusable(symbol: str) -> bool:
            """Reuse book rows; get_quote needs REST-shaped rows (ohlc/depth)."""
            row = _find_quote_row(book_partial, symbol) or {}
            if _pick_float(row, "last_price", "ltp", "last") is None:
                return False
            if prefer != "get_quote":
                return True
            return isinstance(row.get("ohlc"), dict) or "depth" in row

        missing = [sym for sym in symbols if not _row_reusable(sym)]
        # Full book hit: reuse without another sandbox round-trip.
        if book_partial and not missing:
            reused = book_partial
            if ticker_live and overlay_ticker_rows is not None:
                reused = overlay_ticker_rows(book_partial, ticker_live)
            await _cache_set(tenant_id, cached_key, "broker", reused)
            self._last_quote_error = None
            return reused

        fns = await self._quote_tools()
        if not fns:
            if book_partial:
                await _cache_set(tenant_id, cached_key, "broker", book_partial)
                self._last_quote_error = None
                return book_partial
            return {}

        if prefer:
            preferred = [fn for fn in fns if getattr(fn, "__name__", "") == prefer]
            fns = preferred + [fn for fn in fns if fn not in preferred]
        else:
            fns.sort(
                key=lambda fn: QUOTE_TOOL_PRIORITY.get(getattr(fn, "__name__", ""), 99)
            )

        fetch_symbols = missing or symbols
        merged: dict[str, Any] = {}
        # Bound wait tightly on pilot — long quote waits + Tier B races were
        # wiping the board with empty ``starting`` frames.
        quote_timeout_s = float(timeout_s) if timeout_s is not None else 12.0
        quote_timeout_s = max(3.0, min(quote_timeout_s, 20.0))
        # Prefer a single attempt when the caller already shortened the budget.
        max_attempts = 1 if quote_timeout_s <= 10.0 else 2
        for _attempt in range(max_attempts):
            for fn in fns:
                for kwargs in quote_call_attempts(fn, fetch_symbols):
                    try:
                        # Shield + bound wait: SSE cancel must not orphan a
                        # sandbox slot for the full wall clock.
                        result = await asyncio.wait_for(
                            asyncio.shield(invoke_tool(fn, kwargs)),
                            timeout=quote_timeout_s,
                        )
                    except asyncio.CancelledError:
                        raise
                    except TimeoutError:
                        self._last_quote_error = "quote tool timed out"
                        logger.warning(
                            "broker_quote_invoke_timeout",
                            tool=getattr(fn, "__name__", "?"),
                            symbols=fetch_symbols[:8],
                            timeout_s=quote_timeout_s,
                        )
                        continue
                    except Exception as exc:
                        err = str(exc)
                        self._last_quote_error = err
                        logger.warning(
                            "broker_quote_invoke_failed",
                            tool=getattr(fn, "__name__", "?"),
                            symbols=fetch_symbols[:8],
                            error=err[:240],
                        )
                        continue
                    if isinstance(result, dict) and result.get("ok") is False:
                        err = str(
                            result.get("error")
                            or result.get("message")
                            or "quote tool returned ok=false"
                        )
                        self._last_quote_error = err
                        logger.warning(
                            "broker_quote_tool_not_ok",
                            tool=getattr(fn, "__name__", "?"),
                            error=err[:240],
                        )
                        continue
                    merged.update(_normalize_quote_payload(result))
                if merged:
                    break
            if merged:
                break
            await asyncio.sleep(0.35)
        # Prefer live ticker LTP over REST; fall back to REST book on total miss.
        if ticker_live and overlay_ticker_rows is not None:
            merged = overlay_ticker_rows(merged, ticker_live)
        if book_partial and not merged:
            merged = dict(book_partial)
        elif book_partial and merged:
            # Keep already-known symbols when this fetch only covered `missing`.
            for key, row in book_partial.items():
                if key not in merged and isinstance(row, dict):
                    merged[key] = row
        # Never cache empty broker misses — a transient sandbox/token failure
        # must not poison Options Lab / signal ticks for the quote TTL window.
        if merged:
            self._last_quote_error = None
            await _cache_set(tenant_id, cached_key, "broker", merged)
            if write_rest_quote_book is not None:
                try:
                    await write_rest_quote_book(
                        tenant_id,
                        {
                            key: row
                            for key, row in merged.items()
                            if key != "_flat" and isinstance(row, dict)
                        },
                    )
                except Exception as exc:
                    logger.warning(
                        "rest_quote_book_write_failed",
                        error=str(exc)[:200],
                    )
        return merged

    async def _tier_a_quotes(self, symbols: list[str]) -> dict[str, Any]:
        """Assemble underlying/FUT/CE/PE from the live ticker book; REST only for gaps.

        When the ticker heartbeat is dead, REST gap-fill is rate-limited
        (``TIER_A_REST_GAP_FILL_MS``) so a 200ms worker loop cannot stampede the
        sandbox. Prefer stale/REST-seeded book rows over a fresh sandbox call.
        """
        symbols = list(dict.fromkeys(s for s in symbols if s))
        if not symbols:
            return {}
        tenant_id = _tenant_key(self.context)
        book: dict[str, Any] = {}
        soft_book: dict[str, Any] = {}
        ticker_alive = False
        assemble_quotes_from_book = None
        try:
            from app.domains.kite_ticker_hub import (
                TICKER_ALIVE_KEY,
                assemble_quotes_from_book as _assemble,
            )

            assemble_quotes_from_book = _assemble
            alive = await cache.get_metric(tenant_id, TICKER_ALIVE_KEY)
            ticker_alive = isinstance(alive, dict)
            book = (
                await assemble_quotes_from_book(
                    tenant_id,
                    symbols,
                    require_all=False,
                    require_alive=True,
                )
                or {}
            )
        except Exception:
            book = {}
            ticker_alive = False

        out = dict(book)
        missing = [
            sym
            for sym in symbols
            if _pick_float(
                _find_quote_row(out, sym) or {},
                "last_price",
                "ltp",
                "last",
            )
            is None
        ]
        if missing and assemble_quotes_from_book is not None:
            # Reuse REST-seeded / recently written rows without the alive gate.
            try:
                soft_book = (
                    await assemble_quotes_from_book(
                        tenant_id,
                        missing,
                        require_all=False,
                        require_alive=False,
                    )
                    or {}
                )
            except Exception:
                soft_book = {}
            for sym in list(missing):
                row = _find_quote_row(soft_book, sym)
                if (
                    row is not None
                    and _pick_float(row, "last_price", "ltp", "last") is not None
                ):
                    out[sym] = row
            missing = [
                sym
                for sym in missing
                if _pick_float(
                    _find_quote_row(out, sym) or {},
                    "last_price",
                    "ltp",
                    "last",
                )
                is None
            ]

        if not missing:
            return out

        # Ticker alive + partial miss (e.g. new ATM CE/PE) → fill immediately.
        # Ticker dead → rate-limit REST, but still allow symbols we have not
        # recently gap-filled (auto-ATM CE/PE derived after the under/FUT fetch).
        if not ticker_alive:
            gate = await cache.get_metric(tenant_id, "tier_a_rest_gap")
            if isinstance(gate, dict):
                recently = {
                    str(s) for s in (gate.get("missing") or []) if s
                }
                fresh_missing = [s for s in missing if s not in recently]
                if not fresh_missing:
                    return out
                missing = fresh_missing
            await cache.set_metric(
                tenant_id,
                "tier_a_rest_gap",
                "broker",
                {
                    "missing": list(
                        {
                            *(
                                list((gate or {}).get("missing") or [])
                                if isinstance(gate, dict)
                                else []
                            ),
                            *missing,
                        }
                    )[:16],
                    "ts": int(time.time()),
                },
                ttl_ms=TIER_A_REST_GAP_FILL_MS,
            )

        out.update(
            await self._fetch_quote(missing, prefer="get_ltp", timeout_s=10.0)
        )
        return out

    async def refresh_tier_b_context(self, config: SignalEngineConfig) -> None:
        """Refresh crude / VIX / aux quote caches off the Tier A critical path.

        Safe to call from a background task; uses medium-tier TTLs so repeats
        within ~60s are no-ops via cache hits.
        """
        if config.mock:
            return
        tenant_id = _tenant_key(self.context)

        # Dow — slow/manual only (never broker-fetch here).
        if config.dow_change_pct is not None:
            await _cache_set(tenant_id, "dow_jones", "slow", config.dow_change_pct)

        crude_cached = await _cache_get(tenant_id, "crude_oil")
        if crude_cached is None and config.crude_symbol:
            crude_q = await self._fetch_quote([config.crude_symbol])
            crude_row = _find_quote_row(crude_q, config.crude_symbol)
            if crude_row:
                ltp = _pick_float(crude_row, "last_price", "ltp", "last")
                prev = _pick_float(crude_row, "close", "previous_close")
                ohlc = (
                    crude_row.get("ohlc")
                    if isinstance(crude_row.get("ohlc"), dict)
                    else {}
                )
                if prev is None and isinstance(ohlc, dict):
                    prev = _pick_float(ohlc, "close")
                await _cache_set(
                    tenant_id,
                    "crude_oil",
                    "medium",
                    {"crude_ltp": ltp, "crude_prev_close": prev},
                )

        if config.india_vix is None and config.india_vix_symbol:
            vix_cached = await _cache_get(tenant_id, "india_vix")
            if vix_cached is None:
                vix_symbols = list(
                    dict.fromkeys(
                        [
                            config.india_vix_symbol,
                            resolve_kite_instrument(config.india_vix_symbol),
                            "NSE:INDIA VIX",
                            "NSE:INDIAVIX",
                        ]
                    )
                )
                vix_q = await self._fetch_quote(vix_symbols, prefer="get_ltp")
                vix_row = None
                for sym in vix_symbols:
                    vix_row = _find_quote_row(vix_q, sym)
                    if vix_row:
                        break
                if vix_row:
                    vix_ltp = _pick_float(vix_row, "last_price", "ltp", "last")
                    if vix_ltp is not None:
                        await _cache_set(tenant_id, "india_vix", "medium", vix_ltp)

        aux_cached = await _cache_get(tenant_id, "aux_quotes")
        if not isinstance(aux_cached, dict):
            aux_symbols = list(
                dict.fromkeys(
                    [
                        *INDEX_KITE_SYMBOLS.values(),
                        *STOCK_KITE_SYMBOLS.values(),
                        USD_INR_KITE_SYMBOL,
                    ]
                )
            )
            # get_quote carries ohlc / net_change so stock big-move % can populate.
            aux_quotes = (
                await self._fetch_quote(aux_symbols, prefer="get_quote")
                if aux_symbols
                else {}
            )
            aux_payload: dict[str, Any] = {}
            scratch: dict[str, Any] = {}
            _apply_quote_pct_map(scratch, aux_quotes, INDEX_KITE_SYMBOLS)
            _apply_quote_pct_map(scratch, aux_quotes, STOCK_KITE_SYMBOLS)
            for key, val in scratch.items():
                if key.startswith(("index_", "stock_")):
                    aux_payload[key] = val
            usd_row = _find_quote_row(aux_quotes, USD_INR_KITE_SYMBOL)
            usd_ltp = _pick_float(usd_row or {}, "last_price", "ltp", "last")
            if usd_ltp is not None:
                aux_payload["usd_inr"] = usd_ltp
            sensex_row = _find_quote_row(
                aux_quotes, INDEX_KITE_SYMBOLS["index_sensex_chg"]
            )
            if sensex_row:
                sensex_ltp = _pick_float(sensex_row, "last_price", "ltp", "last")
                ohlc = (
                    sensex_row.get("ohlc")
                    if isinstance(sensex_row.get("ohlc"), dict)
                    else {}
                )
                sensex_open = _pick_float(ohlc, "open") if ohlc else None
                if sensex_ltp is not None and sensex_open is not None:
                    aux_payload["sensex_points_move"] = round(
                        sensex_ltp - sensex_open, 2
                    )
            if aux_payload:
                await _cache_set(tenant_id, "aux_quotes", "medium", aux_payload)

    async def _build_feed(self, config: SignalEngineConfig) -> dict[str, Any]:
        tenant_id = _tenant_key(self.context)

        if config.mock:
            return _mock_feed_live(config)

        # Leave headroom under STATE_COMPUTE_TIMEOUT so we can still evaluate
        # and publish a live frame instead of wiping the desk with ``starting``.
        deadline = time.monotonic() + max(8.0, (STATE_COMPUTE_TIMEOUT_MS / 1000) * 0.65)

        def _budget(min_s: float = 3.0) -> bool:
            return (deadline - time.monotonic()) >= min_s

        def _remaining() -> float:
            return max(0.0, deadline - time.monotonic())

        feed: dict[str, Any] = {"source": "live"}

        # Apply Tier B caches first so VIX / stock big-move survive even when
        # later REST fan-out is truncated by the budget.
        vix_cached = await _cache_get(tenant_id, "india_vix")
        if vix_cached is not None and config.india_vix is None:
            try:
                feed["india_vix"] = float(vix_cached)
            except (TypeError, ValueError):
                pass
        aux_cached = await _cache_get(tenant_id, "aux_quotes")
        if isinstance(aux_cached, dict):
            feed.update(aux_cached)
        crude_cached = await _cache_get(tenant_id, "crude_oil")
        if isinstance(crude_cached, dict):
            feed.update(crude_cached)

        # Slow — Dow Jones (manual override or cached once per hour); never REST here.
        dow_cached = await _cache_get(tenant_id, "dow_jones")
        if dow_cached is not None:
            feed["dow_change_pct"] = dow_cached
        elif config.dow_change_pct is not None:
            feed["dow_change_pct"] = config.dow_change_pct
            await _cache_set(tenant_id, "dow_jones", "slow", config.dow_change_pct)

        await _merge_nse_slow_tier(tenant_id, feed, config, mock=False)
        if config.fii_net is not None:
            feed["fii_net"] = config.fii_net

        # Fast — Tier A from live ticker book; REST only for missing LTP rows / IV.
        atm_strike: int | None = None
        spot_row: dict[str, Any] | None = None
        fast_symbols: list[str] = []
        if config.underlying_symbol:
            fast_symbols.append(config.underlying_symbol)
        if config.nifty_fut_symbol:
            fast_symbols.append(config.nifty_fut_symbol)
        if config.india_vix_symbol:
            fast_symbols.append(config.india_vix_symbol)
            # Alias often present on the ticker book / REST.
            fast_symbols.append("NSE:INDIA VIX")
            fast_symbols.append("NSE:INDIAVIX")
        # CE/PE resolved after ATM; seed only real option contracts (never FUT).
        ce_seed = _sanitize_option_symbol(config.ce_symbol)
        pe_seed = _sanitize_option_symbol(config.pe_symbol)
        if ce_seed:
            fast_symbols.append(ce_seed)
        if pe_seed:
            fast_symbols.append(pe_seed)
        fast_symbols = list(dict.fromkeys(fast_symbols))
        fast_quotes: dict[str, Any] = await self._tier_a_quotes(fast_symbols)

        # India VIX — prefer ticker book (REST often empty / Tier B may lag).
        if feed.get("india_vix") is None and config.india_vix is None:
            for vix_sym in (
                config.india_vix_symbol,
                "NSE:INDIA VIX",
                "NSE:INDIAVIX",
            ):
                if not vix_sym:
                    continue
                vix_row = _find_quote_row(fast_quotes, vix_sym)
                vix_ltp = _pick_float(vix_row or {}, "last_price", "ltp", "last")
                if vix_ltp is not None:
                    feed["india_vix"] = vix_ltp
                    await _cache_set(tenant_id, "india_vix", "medium", vix_ltp)
                    break

        # Underlying LTP → ATM
        if not config.underlying_symbol:
            feed["underlying_missing"] = True
        else:
            spot_row = _find_quote_row(fast_quotes, config.underlying_symbol)
            spot_ltp = _pick_float(spot_row or {}, "last_price", "ltp", "last")
            if spot_ltp is not None:
                feed["nifty_ltp"] = spot_ltp
                feed["underlying_symbol"] = config.underlying_symbol
                feed["underlying_label"] = config.underlying_label or config.underlying_symbol
                atm_strike = _round_strike(spot_ltp, config.strike_step)
                feed["atm"] = atm_strike
                vs_prev, vs_open = _quote_change_pcts(spot_row)
                if vs_prev is not None:
                    feed["spot_chg"] = vs_prev
                if vs_open is not None:
                    feed["spot_vs_open"] = vs_open
                ohlc = spot_row.get("ohlc") if isinstance(spot_row.get("ohlc"), dict) else {}
                open_ltp = _pick_float(ohlc, "open") if ohlc else None
                if open_ltp is not None:
                    session_key = f"underlying_open:{_ist_session_date()}"
                    cached_open = await cache.get_session_value(tenant_id, session_key)
                    if cached_open is None:
                        await cache.set_session_value(tenant_id, session_key, open_ltp)
                        cached_open = open_ltp
                    feed["_session_open_ltp"] = cached_open
                    feed["nifty_points_move"] = round(spot_ltp - float(cached_open), 2)

        ce_symbol, pe_symbol = _resolve_option_symbols(config, atm_strike)
        if ce_symbol:
            feed["ce_symbol"] = ce_symbol
        if pe_symbol:
            feed["pe_symbol"] = pe_symbol

        # Re-fetch when auto ATM symbols differ from the first batch (book-first).
        # Use get_quote directly so dead-ticker REST gap gates from the under/FUT
        # fetch cannot skip newly derived CE/PE for the rest of the window.
        extra_symbols = [
            sym
            for sym in (ce_symbol, pe_symbol)
            if sym and _find_quote_row(fast_quotes, sym) is None
        ]
        if extra_symbols:
            # LTP first (cheap); full quote only if budget remains.
            prefer = "get_ltp" if not _budget(12.0) else "get_quote"
            fast_quotes.update(
                await self._fetch_quote(
                    extra_symbols,
                    prefer=prefer,
                    timeout_s=min(10.0, max(3.0, _remaining() - 2.0)),
                )
            )

        ce_row: dict[str, Any] | None = None
        pe_row: dict[str, Any] | None = None
        if ce_symbol:
            ce_row = _find_quote_row(fast_quotes, ce_symbol)
            if ce_row:
                feed["ce"] = _pick_float(ce_row, "last_price", "ltp", "last")
                ce_oi = _pick_float(ce_row, "oi", "open_interest")
                if ce_oi is not None:
                    feed["ce_oi"] = ce_oi
        if pe_symbol:
            pe_row = _find_quote_row(fast_quotes, pe_symbol)
            if pe_row:
                feed["pe"] = _pick_float(pe_row, "last_price", "ltp", "last")
                pe_oi = _pick_float(pe_row, "oi", "open_interest")
                if pe_oi is not None:
                    feed["pe_oi"] = pe_oi

        # After preset switch, get_quote can miss while get_ltp still prints.
        if (ce_symbol or pe_symbol) and (
            feed.get("ce") is None or feed.get("pe") is None
        ):
            retry_syms = [
                s
                for s in (ce_symbol, pe_symbol)
                if s
                and (
                    (s == ce_symbol and feed.get("ce") is None)
                    or (s == pe_symbol and feed.get("pe") is None)
                )
            ]
            if retry_syms:
                fast_quotes.update(
                    await self._fetch_quote(
                        retry_syms,
                        prefer="get_ltp",
                        timeout_s=min(8.0, max(3.0, _remaining() - 2.0)),
                    )
                )
                if ce_symbol and feed.get("ce") is None:
                    ce_row = _find_quote_row(fast_quotes, ce_symbol) or ce_row
                    if ce_row:
                        feed["ce"] = _pick_float(ce_row, "last_price", "ltp", "last")
                if pe_symbol and feed.get("pe") is None:
                    pe_row = _find_quote_row(fast_quotes, pe_symbol) or pe_row
                    if pe_row:
                        feed["pe"] = _pick_float(pe_row, "last_price", "ltp", "last")

        if _budget(10.0):
            await _merge_secondary_ce_pe_quotes(self, config, feed, fast_quotes)
        else:
            logger.info(
                "signal_feed_skip_secondary_ce_pe",
                tenant_id=tenant_id,
                reason="compute_budget",
            )

        ce_vol = _pick_float(ce_row or {}, "volume") if ce_row else None
        pe_vol = _pick_float(pe_row or {}, "volume") if pe_row else None
        if ce_vol is not None or pe_vol is not None:
            feed["atm_volume"] = (ce_vol or 0) + (pe_vol or 0)

        iv_val = _merge_option_iv(ce_row, pe_row)
        if iv_val is not None:
            feed["iv"] = iv_val
        elif ce_row:
            iv_from_ce = _pick_float(ce_row, "implied_volatility", "iv")
            if iv_from_ce is not None:
                feed["iv"] = iv_from_ce
        # Ticker packets usually lack IV — REST when needed, medium-cached.
        if feed.get("iv") is None and (ce_symbol or pe_symbol) and _budget(8.0):
            iv_cached = await _cache_get(tenant_id, "atm_iv")
            if iv_cached is not None:
                feed["iv"] = iv_cached
            else:
                iv_syms = [s for s in (ce_symbol, pe_symbol) if s]
                iv_quotes = await self._fetch_quote(
                    iv_syms,
                    prefer="get_quote",
                    timeout_s=min(10.0, max(3.0, _remaining() - 2.0)),
                )
                if ce_symbol:
                    ce_row = _find_quote_row(iv_quotes, ce_symbol) or ce_row
                if pe_symbol:
                    pe_row = _find_quote_row(iv_quotes, pe_symbol) or pe_row
                iv_val = _merge_option_iv(ce_row, pe_row)
                if iv_val is not None:
                    feed["iv"] = iv_val
                    await _cache_set(tenant_id, "atm_iv", "medium", iv_val)

        # OI — nearest fut if configured (book first; REST only when OI missing)
        fut_row: dict[str, Any] | None = None
        if config.nifty_fut_symbol:
            fut_row = _find_quote_row(fast_quotes, config.nifty_fut_symbol)
            oi_val = _pick_float(fut_row or {}, "oi", "open_interest") if fut_row else None
            if oi_val is None and _budget(8.0):
                fut_quotes = await self._fetch_quote(
                    [config.nifty_fut_symbol],
                    prefer="get_quote",
                    timeout_s=min(8.0, max(3.0, _remaining() - 2.0)),
                )
                fut_row = _find_quote_row(fut_quotes, config.nifty_fut_symbol) or fut_row
                oi_val = _pick_float(fut_row or {}, "oi", "open_interest") if fut_row else None
            elif oi_val is None:
                logger.info(
                    "signal_feed_skip_fut_oi",
                    tenant_id=tenant_id,
                    reason="compute_budget",
                )
            if oi_val is not None:
                feed["oi"] = oi_val
                baseline_key = _oi_baseline_cache_key()
                prev_oi = await cache.get_session_value(tenant_id, baseline_key)
                if prev_oi is not None and prev_oi != 0 and config.oi_pct_chg is None:
                    feed["oi_pct_chg"] = ((oi_val - float(prev_oi)) / float(prev_oi)) * 100
                if prev_oi is None:
                    await cache.set_session_value(tenant_id, baseline_key, oi_val)
            fut_ltp = _pick_float(fut_row or {}, "last_price", "ltp", "last")
            basis = _fut_basis_pct(feed.get("nifty_ltp"), fut_ltp)
            if basis is not None:
                feed["fut_basis"] = basis

        # Tier B crude already applied from cache at the top of _build_feed.

        # IV day-high + session open for iv_chg
        iv = feed.get("iv")
        if iv is not None:
            iv_f = float(iv)
            stored_high = await cache.get_session_value(tenant_id, _session_dated_key("iv_day_high"))
            current_high = max(float(stored_high), iv_f) if stored_high is not None else iv_f
            await cache.set_session_value(tenant_id, _session_dated_key("iv_day_high"), current_high)
            feed["iv_day_high"] = current_high
            session_open = await cache.get_session_value(tenant_id, _session_dated_key("iv_session_open"))
            if session_open is None:
                await cache.set_session_value(tenant_id, _session_dated_key("iv_session_open"), iv_f)
                session_open = iv_f
            if config.iv_chg is None:
                feed["iv_chg"] = iv_f - float(session_open)

        # Sensibull-aligned fields — chain OI is expensive; skip when budget is tight.
        if _budget(12.0):
            await _merge_option_chain_tier(
                self,
                tenant_id,
                feed,
                config,
                atm_strike=atm_strike,
                mock=False,
            )
        else:
            logger.info(
                "signal_feed_skip_option_chain",
                tenant_id=tenant_id,
                reason="compute_budget",
            )
            cached_chain = await _cache_get(tenant_id, "option_chain")
            if isinstance(cached_chain, dict):
                _merge_chain_payload(feed, cached_chain, config)
        if config.pcr is None and feed.get("pcr") is None:
            estimated_pcr = _estimate_pcr(ce_row, pe_row)
            if estimated_pcr is not None:
                feed["pcr"] = estimated_pcr
                feed["pcr_source"] = "atm_oi"

        sensibull_fields = {
            "max_pain": config.max_pain,
            "pcr": config.pcr,
            "ivp": config.ivp,
            "oi_pct_chg": config.oi_pct_chg,
            "iv_chg": config.iv_chg,
            "india_vix": config.india_vix,
        }
        for key, val in sensibull_fields.items():
            if val is not None:
                feed[key] = val
                if key == "pcr":
                    feed["pcr_source"] = "manual"

        # ADX + RSI from Kite historical candles (medium tier)
        trend_cached = await _cache_get(tenant_id, "trend")
        if isinstance(trend_cached, dict):
            if trend_cached.get("adx") is not None:
                feed["adx"] = trend_cached["adx"]
            if trend_cached.get("rsi") is not None:
                feed["rsi"] = trend_cached["rsi"]
        elif spot_row is not None and _budget(10.0):
            token_raw = spot_row.get("instrument_token")
            try:
                token = int(token_raw) if token_raw is not None else 0
            except (TypeError, ValueError):
                token = 0
            if token > 0:
                # Kite toolkit takes from_date/to_date (not years/months/days).
                now = _ist_now()
                hist_from = (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
                hist_to = now.strftime("%Y-%m-%d %H:%M:%S")
                hist = await self._invoke_broker_tool(
                    "get_historical_candles",
                    {
                        "instrument_token": token,
                        "interval": "minute",
                        "from_date": hist_from,
                        "to_date": hist_to,
                    },
                )
                highs, lows, closes = _parse_historical_candles(hist)
                trend_payload: dict[str, Any] = {}
                adx_val = _compute_adx(highs, lows, closes)
                if adx_val is not None:
                    trend_payload["adx"] = adx_val
                    feed["adx"] = adx_val
                rsi_val = _compute_rsi(closes)
                if rsi_val is not None:
                    trend_payload["rsi"] = rsi_val
                    feed["rsi"] = rsi_val
                if trend_payload:
                    await _cache_set(tenant_id, "trend", "medium", trend_payload)

        if _budget(12.0):
            await _merge_levels_tier(
                self,
                tenant_id,
                feed,
                spot_row=spot_row,
                mock=False,
            )
        else:
            levels_cached = await _cache_get(tenant_id, "levels")
            if isinstance(levels_cached, dict):
                feed.update(levels_cached)
            _refresh_level_spot_fields(feed, spot_row)
            logger.info(
                "signal_feed_skip_levels_hist",
                tenant_id=tenant_id,
                reason="compute_budget",
            )

        # India VIX / aux — already seeded from cache at top; refresh only if still missing
        # and budget remains (background Tier B usually fills these).
        if feed.get("india_vix") is None and config.india_vix_symbol and _budget(5.0):
            vix_cached = await _cache_get(tenant_id, "india_vix")
            if vix_cached is not None:
                try:
                    feed["india_vix"] = float(vix_cached)
                except (TypeError, ValueError):
                    pass
        if feed.get("india_vix") is not None and feed.get("vix_chg") is None:
            vix_ltp = float(feed["india_vix"])
            session_vix = await cache.get_session_value(tenant_id, _session_dated_key("vix_session_open"))
            if session_vix is None:
                await cache.set_session_value(tenant_id, _session_dated_key("vix_session_open"), vix_ltp)
                session_vix = vix_ltp
            feed["vix_chg"] = round(vix_ltp - float(session_vix), 3)

        if not any(k.startswith("stock_") for k in feed):
            aux_cached = await _cache_get(tenant_id, "aux_quotes")
            if isinstance(aux_cached, dict):
                feed.update(aux_cached)

        if _budget(6.0):
            await _merge_yahoo_slow_tier(tenant_id, feed, mock=False)
            await _merge_yahoo_timing_tier(tenant_id, feed, mock=False)
        if feed.get("ce") is not None and feed.get("pe") is not None:
            feed["straddle"] = round(float(feed["ce"]) + float(feed["pe"]), 2)
        await _apply_straddle_decay(tenant_id, feed)
        _enrich_derived_feed_fields(feed)

        return feed

    async def state(self) -> dict[str, Any]:
        config, has_broker, team_ready = await self._load_setup()
        tenant_id = _tenant_key(self.context)
        if not config.engine_enabled:
            frozen = await cache.get_snapshot(tenant_id)
            if frozen is not None:
                return {
                    **frozen,
                    "engine_enabled": False,
                    "engine_active": False,
                    "has_broker": has_broker,
                    "team_slug": SIGNAL_TEAM_SLUG,
                    "stream": True,
                }
            feed: dict[str, Any] = {"source": "stopped"}
            payload = evaluate_signal_state(config, feed)
            payload["mock"] = config.mock
            payload["live"] = False
            payload["engine_enabled"] = False
            payload["engine_active"] = False
            payload["has_broker"] = has_broker
            payload["team_slug"] = SIGNAL_TEAM_SLUG
            payload["live_warnings"] = _live_setup_warnings(
                config,
                feed,
                has_broker=has_broker,
                team_ready=team_ready,
                quote_error=self._last_quote_error,
            )
            payload["live_quote_missing"] = False
            payload["underlying"] = {
                "symbol": config.underlying_symbol,
                "label": config.underlying_label or config.underlying_symbol or "—",
            }
            payload["broker_poll_ms"] = BROKER_QUOTE_TTL_MS
            payload["stream"] = True
            return payload

        feed = await self._build_feed(config)
        payload = evaluate_signal_state(config, feed)
        payload["mock"] = config.mock
        payload["live"] = not config.mock
        payload["engine_enabled"] = True
        payload["engine_active"] = True
        payload["has_broker"] = has_broker
        payload["team_slug"] = SIGNAL_TEAM_SLUG
        payload["live_warnings"] = _live_setup_warnings(
            config,
            feed,
            has_broker=has_broker,
            team_ready=team_ready,
            quote_error=self._last_quote_error,
        )
        # Explicit flag so the desk badge does not regex-match warning prose.
        payload["live_quote_missing"] = (not config.mock) and feed.get("nifty_ltp") is None
        payload["underlying"] = {
            "symbol": config.underlying_symbol,
            "label": config.underlying_label or config.underlying_symbol or "—",
        }
        payload["broker_poll_ms"] = BROKER_QUOTE_TTL_MS
        payload["stream"] = True
        try:
            from app.domains.kite_ticker_hub import read_ticker_health

            payload["ticker"] = await read_ticker_health(tenant_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "signal_ticker_health_failed",
                tenant_id=tenant_id,
                error=str(exc)[:160],
            )
            payload["ticker"] = {"path": "unknown", "connected": False}
        # Surface resolved ATM options for UI + auto-persist.
        if feed.get("ce_symbol"):
            payload["ce_symbol"] = feed.get("ce_symbol")
        if feed.get("pe_symbol"):
            payload["pe_symbol"] = feed.get("pe_symbol")
        if feed.get("atm") is not None:
            payload["atm"] = feed.get("atm")
        # Persist empty/mismatched CE/PE so setup bar + ticker stay in sync.
        try:
            await self.maybe_persist_auto_atm_symbols(payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "signal_auto_atm_persist_failed",
                tenant_id=_tenant_key(self.context),
                error=str(exc)[:160],
            )
        return payload

    async def publish_entry(
        self,
        *,
        title: str | None = None,
        body: str | None = None,
    ) -> dict[str, Any]:
        snapshot = await self.state()
        entry = snapshot.get("entry")
        if not entry:
            return {"ok": False, "error": "Entry conditions not met", "snapshot": snapshot}

        label = str(entry.get("label") or "New signal")
        notify_title = (title or "New trading signal").strip() or "New trading signal"
        notify_body = (body or label).strip() or label

        tenant_id = _tenant_key(self.context)
        signature = json.dumps({"entry": entry}, sort_keys=True)
        last_signature = await cache.get_session_value(tenant_id, "last_entry_signature")
        if last_signature == signature:
            return {
                "ok": True,
                "deduped": True,
                "entry": entry,
                "snapshot": snapshot,
            }
        await cache.set_session_value(tenant_id, "last_entry_signature", signature)

        from app.db.repositories import MembershipRepository

        memberships = MembershipRepository(self.session, self.context)
        notifications = UserNotificationRepository(self.session, self.context)
        rows = await memberships.list_users()
        recipients = [
            row.user_id
            for row in rows
            if row.is_active and row.user_id and not row.user_id.startswith("invite:")
        ]
        batch_id, created = await notifications.create_batch(
            title=notify_title,
            body=notify_body,
            created_by=self.context.user_id,
            audience="all",
            recipient_user_ids=recipients,
        )
        return {
            "ok": True,
            "entry": entry,
            "notification": {
                "batch_id": str(batch_id),
                "recipient_count": len(created),
                "title": notify_title,
                "body": notify_body,
            },
            "snapshot": snapshot,
        }


def _build_entry_preview(
    config: SignalEngineConfig,
    feed: dict[str, Any],
    *,
    entry_ready: bool,
    passed: int,
    evaluable: int,
) -> dict[str, Any]:
    atm_raw = feed.get("atm")
    atm = int(atm_raw) if atm_raw is not None else None
    ce_live = feed.get("ce")
    pe_live = feed.get("pe")
    ce_p = float(ce_live) if ce_live is not None else config.entry_ce_premium
    pe_p = float(pe_live) if pe_live is not None else config.entry_pe_premium
    exit_p = config.exit_pct
    buy_line = (
        f"BUY= {atm}, CE={ce_p:g}, PE={pe_p:g}, EXIT +{exit_p:g}%"
        if atm is not None
        else f"BUY= —, CE={ce_p:g}, PE={pe_p:g}, EXIT +{exit_p:g}%"
    )
    if entry_ready and atm is not None:
        status = "ready"
        status_note = "All entry rules pass — publish to notify the desk."
    elif evaluable == 0:
        status = "waiting"
        status_note = "Waiting for live broker data and evaluable rules."
    elif passed < evaluable:
        status = "blocked"
        failing = evaluable - passed
        noun = "rule" if failing == 1 else "rules"
        status_note = f"No buy — {failing} {noun} failing ({passed}/{evaluable} pass)."
    else:
        status = "waiting"
        status_note = f"No buy yet — {passed}/{evaluable} rules passing."

    return {
        "side": "BUY",
        "atm": atm,
        "ce": ce_p,
        "pe": pe_p,
        "exit_pct": exit_p,
        "status": status,
        "label": buy_line,
        "status_note": status_note,
    }


def evaluate_signal_state(config: SignalEngineConfig, feed: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    evaluable = 0
    passed = 0
    for spec in config.metrics:
        metric_id = str(spec["id"])
        rule = spec.get("rule", "info")
        target = float(spec.get("target") or 0)
        gates_entry = bool(spec.get("gates_entry", False))
        ce_key = str(spec.get("ce_feed_key") or "ce")
        pe_key = str(spec.get("pe_feed_key") or "pe")
        metric_ce = feed.get(ce_key)
        metric_pe = feed.get(pe_key)
        value = _metric_value(metric_id, feed, spec)
        if rule == "before_time":
            value = feed.get("ist_hour")
            if value is None:
                now = _ist_now()
                value = round(now.hour + now.minute / 60.0, 3)
        if rule == "spot_below_max_pain":
            spot = feed.get("nifty_ltp") or feed.get("atm")
            value = float(spot) if spot is not None else None
        ok = _evaluate_rule(
            rule,  # type: ignore[arg-type]
            float(value) if value is not None else None,
            target,
            feed=feed,
            ce=float(metric_ce) if metric_ce is not None else None,
            pe=float(metric_pe) if metric_pe is not None else None,
            spec=spec,
        )
        display_passed = ok
        if ok is not None and gates_entry:
            evaluable += 1
            if ok:
                passed += 1
        elif rule == "info":
            display_passed = None
        display_value = value
        if rule == "spot_below_max_pain":
            display_value = feed.get("max_pain")
        rows.append(
            {
                "id": metric_id,
                "check_no": spec.get("check_no", 0),
                "category": spec.get("category", ""),
                "label": spec.get("label", metric_id),
                "value": display_value,
                "target": _format_target(rule, target, spec),  # type: ignore[arg-type]
                "rule": rule,
                "tier": spec.get("tier", "fast"),
                "passed": display_passed,
                "gates_entry": gates_entry,
                "hint": spec.get("hint", ""),
                "source": spec.get("source", spec.get("feed_key", "")),
            }
        )

    entry_ready = evaluable > 0 and passed == evaluable
    entry = _build_entry_preview(
        config,
        feed,
        entry_ready=entry_ready,
        passed=passed,
        evaluable=evaluable,
    )
    return {
        "metrics": rows,
        "entry_ready": entry_ready,
        "entry": entry,
        "passed": passed,
        "evaluable": evaluable,
        "feed_source": feed.get("source", "unknown"),
        "evaluated_at": time.time(),
        "poll_ms": STREAM_INTERVAL_MS,
    }
