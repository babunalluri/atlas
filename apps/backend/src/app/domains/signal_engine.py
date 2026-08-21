"""Admin signal engine: tiered metric fetch, entry evaluation, publish hooks.

Metrics are admin-only (Signals ops team). End-user desk never loads this module.
UI pushes at ~8×/sec (SSE); broker quotes refresh ~2×/sec; slow sources cache longer.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.factory import AgentFactoryService, McpToolSkipped
from app.db.repositories import TeamRepository, ToolDefinitionRepository, ToolDefinitionVersionRepository
from app.db.repositories import UserNotificationRepository
from app.domains.desk_snapshot import (
    QUOTE_CAPABILITIES,
    _groww_symbol,
    _looks_like_quote_map,
    _quote_map_rows,
    invoke_tool,
    quote_call_attempts,
)
from app.domains import signal_engine_cache as cache
from app.domains.signal_engine_constants import (
    BROKER_QUOTE_TTL_MS,
    SNAPSHOT_FRESH_MS,
    STREAM_COMPUTE_WAIT_MS,
    STREAM_INTERVAL_MS,
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
    ALL_YAHOO_TICKERS,
    CRYPTO_YAHOO_TICKERS,
    INDEX_KITE_SYMBOLS,
    STOCK_KITE_SYMBOLS,
    TIMING_YAHOO_TICKERS,
    USD_INR_KITE_SYMBOL,
    crypto_max_abs_change,
    fetch_yahoo_changes,
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
    }


def _annotate_snapshot_freshness(payload: dict[str, Any]) -> dict[str, Any]:
    """Attach age + server-side stale flag. Unknown age stays null (not 'fresh')."""
    now_ms = int(time.time() * 1000)
    out = dict(payload)
    out["snapshot_fresh_ms"] = SNAPSHOT_FRESH_MS
    computed = out.get("computed_at_ms")
    if computed is None:
        out["data_age_ms"] = None
        out["snapshot_stale"] = None
        return out
    try:
        computed_ms = int(computed)
    except (TypeError, ValueError):
        out["data_age_ms"] = None
        out["snapshot_stale"] = None
        return out
    age = max(0, now_ms - computed_ms)
    out["data_age_ms"] = age
    out["snapshot_stale"] = age > SNAPSHOT_FRESH_MS
    return out


async def state_for_stream(service: "SignalEngineService") -> dict[str, Any]:
    """Coalesce concurrent stream/poll readers to one engine tick per tenant."""
    tenant_id = _tenant_key(service.context)
    config = await service._load_config()

    snapshot = await cache.get_snapshot(tenant_id)
    if snapshot is not None:
        if not config.engine_enabled:
            return _apply_engine_stopped_overlay(snapshot)
        return _annotate_snapshot_freshness(snapshot)

    if await cache.try_compute_lock(tenant_id):
        heartbeat = cache.start_compute_lock_heartbeat(tenant_id)
        try:
            snapshot = await cache.get_snapshot(tenant_id)
            if snapshot is not None:
                if not config.engine_enabled:
                    return _apply_engine_stopped_overlay(snapshot)
                return _annotate_snapshot_freshness(snapshot)
            payload = await service.state()
            if not config.engine_enabled:
                payload = _apply_engine_stopped_overlay(payload)
            else:
                payload = {
                    **payload,
                    "computed_at_ms": int(time.time() * 1000),
                }
            await cache.set_snapshot(tenant_id, payload)
            return _annotate_snapshot_freshness(payload)
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
            return _annotate_snapshot_freshness(snapshot)

    payload = await service.state()
    if not config.engine_enabled:
        payload = _apply_engine_stopped_overlay(payload)
    else:
        payload = {**payload, "computed_at_ms": int(time.time() * 1000)}
    await cache.set_snapshot(tenant_id, payload)
    return _annotate_snapshot_freshness(payload)

# Admin-selectable underlyings (not hard-coded to NIFTY).
UNDERLYING_PRESETS: list[dict[str, Any]] = [
    {"label": "NIFTY 50", "symbol": "NSE:NIFTY 50", "strike_step": 50},
    {"label": "NIFTY", "symbol": "NSE:NIFTY", "strike_step": 50},
    {"label": "BANKNIFTY", "symbol": "NSE:BANKNIFTY", "strike_step": 100},
    {"label": "FINNIFTY", "symbol": "NSE:FINNIFTY", "strike_step": 50},
    {"label": "NIFTYNXT50", "symbol": "NSE:NIFTYNXT50", "strike_step": 100},
    {"label": "SENSEX", "symbol": "BSE:SENSEX", "strike_step": 100},
    {"label": "MIDCPNIFTY", "symbol": "NSE:MIDCPNIFTY", "strike_step": 25},
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
        raw = settings or {}
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
            ce_symbol=str(raw.get("ce_symbol") or ""),
            pe_symbol=str(raw.get("pe_symbol") or ""),
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
    ce = config.ce_symbol.strip() if config.ce_symbol else ""
    pe = config.pe_symbol.strip() if config.pe_symbol else ""
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
    known_roots = ("MIDCPNIFTY", "BANKNIFTY", "FINNIFTY", "NIFTY50", "NIFTY", "SENSEX")
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
    vs_prev: float | None = None
    vs_open: float | None = None
    if ltp is not None and prev not in (None, 0):
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
        payload = mock_yahoo_changes(ALL_YAHOO_TICKERS)
        crypto = mock_yahoo_changes(CRYPTO_YAHOO_TICKERS)
    else:
        payload = fetch_yahoo_changes(ALL_YAHOO_TICKERS)
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
        intra_from = now.replace(hour=9, minute=15, second=0, microsecond=0).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        intra_to = now.strftime("%Y-%m-%d %H:%M:%S")
        daily_hist = await service._invoke_broker_tool(
            "get_historical_candles",
            {
                "instrument_token": token,
                "interval": "day",
                "from_date": daily_from,
                "to_date": daily_to,
            },
        )
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
        **mock_yahoo_changes(ALL_YAHOO_TICKERS),
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


def _quote_keys_match(quote_key: str, *, norm: str, groww: str) -> bool:
    qk = quote_key.upper().replace(" ", "")
    if qk == norm or qk == groww:
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
    norm = key.upper().replace(" ", "")
    groww = _groww_symbol(key).upper()
    direct = _quote_row_from_value(quotes.get(key))
    if direct is not None:
        return direct
    keyed = {qk: row for qk, row in quotes.items() if qk != "_flat"}
    for quote_key, row in keyed.items():
        if not _quote_keys_match(str(quote_key), norm=norm, groww=groww):
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
    norm = key.upper().replace(" ", "")
    groww = _groww_symbol(key).upper()
    direct = _quote_row_from_value(quotes.get(key))
    if direct is not None:
        return direct
    for quote_key, row in quotes.items():
        if quote_key == "_flat":
            continue
        if not _quote_keys_match(str(quote_key), norm=norm, groww=groww):
            continue
        parsed = _quote_row_from_value(row)
        if parsed is not None:
            return parsed
    return None


def _live_setup_warnings(
    config: SignalEngineConfig,
    feed: dict[str, Any],
    *,
    has_broker: bool,
    team_ready: bool,
) -> list[str]:
    if config.mock:
        return []
    warnings: list[str] = []
    if not team_ready:
        warnings.append("Publish the Signals ops team and bind tools.")
    if not has_broker:
        warnings.append("Bind Kite (recommended) or Groww read-only quotes on Signals ops.")
    if not config.underlying_symbol:
        warnings.append("Select an underlying symbol (Admin → Signal config).")
    elif feed.get("nifty_ltp") is None:
        warnings.append(
            f"No live print for {config.underlying_symbol}. Check broker token and symbol."
        )
    if not config.ce_symbol or not config.pe_symbol:
        warnings.append("Set ce_symbol and pe_symbol in signal engine tool settings.")
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

    @staticmethod
    def _tool_settings(definition: Any) -> dict[str, Any]:
        return dict((getattr(definition, "config", None) or {}).get("settings") or {})

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
                settings.update(self._tool_settings(definition))
            elif definition.kind == "tenant_python" and "signal" not in slug:
                # Broker toolkit — skip merging quote tool settings into signal config.
                continue
            else:
                settings.update(self._tool_settings(definition))
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
        return {
            "config": config.to_admin_dict(),
            "presets": UNDERLYING_PRESETS,
            "tool_bound": tool is not None,
            "tool_slug": tool.slug if tool else None,
        }

    async def update_admin_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        tool = await self._signal_engine_tool()
        if tool is None:
            return {"ok": False, "error": "Signal engine tool not bound on Signals ops team."}
        current = self._tool_settings(tool)
        merged = {**current}
        for key in ADMIN_CONFIG_KEYS:
            if key not in patch:
                continue
            val = patch[key]
            if val is None or val == "":
                merged.pop(key, None)
            else:
                merged[key] = val
        # Keep legacy alias in sync for older readers.
        if merged.get("underlying_symbol"):
            merged["nifty_symbol"] = merged["underlying_symbol"]
        if merged.get("fut_symbol"):
            merged["nifty_fut_symbol"] = merged["fut_symbol"]
        await self._write_tool_settings(tool, merged)
        tenant_id = _tenant_key(self.context)
        if patch.get("engine_enabled") is False:
            await cache.clear_watcher(tenant_id)
        await _invalidate_tenant_signal_cache(tenant_id)
        if patch.get("engine_enabled") is False:
            stopped = _apply_engine_stopped_overlay(await self.state())
            await cache.set_snapshot(tenant_id, stopped)
        elif patch.get("engine_enabled") is True:
            # Start desk watching immediately. Snapshot warm is scheduled from the
            # API via BackgroundTasks after the request transaction commits — a
            # create_task here can race and cache a stopped snapshot.
            # Cache was already invalidated above so a prior stopped overlay cannot
            # linger for SNAPSHOT_TTL_MS.
            await cache.touch_watcher(tenant_id)
        return {"ok": True, **await self.get_admin_config()}

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
            except Exception:
                continue
            callables = built if isinstance(built, list) else [built]
            for fn in callables:
                name = getattr(fn, "__name__", "")
                if name in SIGNAL_BROKER_CAPABILITIES:
                    fns.append(fn)
        return fns

    async def _invoke_broker_tool(self, name: str, kwargs: dict[str, Any]) -> Any | None:
        for fn in await self._broker_tools():
            if getattr(fn, "__name__", "") != name:
                continue
            try:
                return await invoke_tool(fn, kwargs)
            except Exception:
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
    ) -> dict[str, Any]:
        cached_key = f"quote:{prefer or 'auto'}:{','.join(symbols)}"
        tenant_id = _tenant_key(self.context)
        hit = await _cache_get(tenant_id, cached_key)
        if hit is not None:
            return hit

        # Live ticker book overlays REST so LTP/OI stay hot without dropping IV.
        ticker_partial: dict[str, Any] = {}
        try:
            from app.domains.kite_ticker_hub import (
                assemble_quotes_from_book,
                overlay_ticker_rows,
            )

            ticker_partial = (
                await assemble_quotes_from_book(tenant_id, symbols, require_all=False)
                or {}
            )
        except Exception:
            ticker_partial = {}

        # Ticker-only is fine for LTP-style callers. get_quote always hits REST so
        # Options Lab / chain IV-greeks stay intact; ticker then overlays LTP/OI.
        if (
            prefer != "get_quote"
            and ticker_partial
            and len(ticker_partial) == len(symbols)
        ):
            await _cache_set(tenant_id, cached_key, "broker", ticker_partial)
            return ticker_partial

        fns = await self._quote_tools()
        if not fns:
            if ticker_partial:
                await _cache_set(tenant_id, cached_key, "broker", ticker_partial)
                return ticker_partial
            return {}

        if prefer:
            preferred = [fn for fn in fns if getattr(fn, "__name__", "") == prefer]
            fns = preferred + [fn for fn in fns if fn not in preferred]
        else:
            fns.sort(
                key=lambda fn: QUOTE_TOOL_PRIORITY.get(getattr(fn, "__name__", ""), 99)
            )

        merged: dict[str, Any] = {}
        for fn in fns:
            for kwargs in quote_call_attempts(fn, symbols):
                try:
                    result = await invoke_tool(fn, kwargs)
                except Exception:
                    continue
                merged.update(_normalize_quote_payload(result))
            if merged:
                break
        if ticker_partial:
            merged = overlay_ticker_rows(merged, ticker_partial)
        await _cache_set(tenant_id, cached_key, "broker", merged)
        return merged

    async def _build_feed(self, config: SignalEngineConfig) -> dict[str, Any]:
        tenant_id = _tenant_key(self.context)

        if config.mock:
            return _mock_feed_live(config)

        feed: dict[str, Any] = {"source": "live"}

        # Slow — Dow Jones (manual override or cached once per hour)
        dow_cached = await _cache_get(tenant_id, "dow_jones")
        if dow_cached is not None:
            feed["dow_change_pct"] = dow_cached
        elif config.dow_change_pct is not None:
            feed["dow_change_pct"] = config.dow_change_pct
            await _cache_set(tenant_id, "dow_jones", "slow", config.dow_change_pct)

        await _merge_nse_slow_tier(tenant_id, feed, config, mock=False)
        if config.fii_net is not None:
            feed["fii_net"] = config.fii_net

        # Fast — batch broker quotes for this tick (one API call when cache cold)
        atm_strike: int | None = None
        spot_row: dict[str, Any] | None = None
        fast_symbols: list[str] = []
        if config.underlying_symbol:
            fast_symbols.append(config.underlying_symbol)
        if config.nifty_fut_symbol:
            fast_symbols.append(config.nifty_fut_symbol)
        # CE/PE symbols resolved after ATM is known; seed configured symbols for first pass.
        if config.ce_symbol:
            fast_symbols.append(config.ce_symbol)
        if config.pe_symbol:
            fast_symbols.append(config.pe_symbol)
        fast_symbols = list(dict.fromkeys(fast_symbols))
        fast_quotes: dict[str, Any] = (
            await self._fetch_quote(fast_symbols) if fast_symbols else {}
        )

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

        # Re-fetch when auto ATM symbols differ from the first batch.
        extra_symbols = [
            sym
            for sym in (ce_symbol, pe_symbol)
            if sym and _find_quote_row(fast_quotes, sym) is None
        ]
        if extra_symbols:
            fast_quotes.update(await self._fetch_quote(extra_symbols))

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

        await _merge_secondary_ce_pe_quotes(self, config, feed, fast_quotes)

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

        # OI — nearest fut if configured (requires full quote, not LTP-only)
        fut_row: dict[str, Any] | None = None
        if config.nifty_fut_symbol:
            fut_row = _find_quote_row(fast_quotes, config.nifty_fut_symbol)
            oi_val = _pick_float(fut_row or {}, "oi", "open_interest") if fut_row else None
            if oi_val is None:
                fut_quotes = await self._fetch_quote(
                    [config.nifty_fut_symbol],
                    prefer="get_quote",
                )
                fut_row = _find_quote_row(fut_quotes, config.nifty_fut_symbol)
                oi_val = _pick_float(fut_row or {}, "oi", "open_interest") if fut_row else None
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

        # Crude — medium tier cache
        crude_cached = await _cache_get(tenant_id, "crude_oil")
        if crude_cached is not None:
            feed.update(crude_cached)
        elif config.crude_symbol:
            crude_q = await self._fetch_quote([config.crude_symbol])
            crude_row = _find_quote_row(crude_q, config.crude_symbol)
            if crude_row:
                ltp = _pick_float(crude_row, "last_price", "ltp", "last")
                prev = _pick_float(crude_row, "close", "previous_close")
                ohlc = crude_row.get("ohlc") if isinstance(crude_row.get("ohlc"), dict) else {}
                if prev is None and isinstance(ohlc, dict):
                    prev = _pick_float(ohlc, "close")
                payload = {"crude_ltp": ltp, "crude_prev_close": prev}
                feed.update(payload)
                await _cache_set(tenant_id, "crude_oil", "medium", payload)

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

        # Sensibull-aligned fields — chain OI first, then ATM estimate, manual override last
        await _merge_option_chain_tier(
            self,
            tenant_id,
            feed,
            config,
            atm_strike=atm_strike,
            mock=False,
        )
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
        elif spot_row is not None:
            token_raw = spot_row.get("instrument_token")
            try:
                token = int(token_raw) if token_raw is not None else 0
            except (TypeError, ValueError):
                token = 0
            if token > 0:
                now = _ist_now()
                from_dt = (now - timedelta(days=ADX_LOOKBACK_DAYS)).replace(
                    hour=9, minute=15, second=0, microsecond=0
                )
                hist = await self._invoke_broker_tool(
                    "get_historical_candles",
                    {
                        "instrument_token": token,
                        "interval": ADX_CANDLE_INTERVAL,
                        "from_date": from_dt.strftime("%Y-%m-%d %H:%M:%S"),
                        "to_date": now.strftime("%Y-%m-%d %H:%M:%S"),
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

        await _merge_levels_tier(
            self,
            tenant_id,
            feed,
            spot_row=spot_row,
            mock=False,
        )

        # India VIX quote (medium tier) when not set manually
        if feed.get("india_vix") is None and config.india_vix_symbol:
            vix_cached = await _cache_get(tenant_id, "india_vix")
            if vix_cached is not None:
                feed["india_vix"] = vix_cached
            else:
                vix_q = await self._fetch_quote([config.india_vix_symbol])
                vix_row = _find_quote_row(vix_q, config.india_vix_symbol)
                if vix_row:
                    vix_ltp = _pick_float(vix_row, "last_price", "ltp", "last")
                    if vix_ltp is not None:
                        feed["india_vix"] = vix_ltp
                        await _cache_set(tenant_id, "india_vix", "medium", vix_ltp)
        if feed.get("india_vix") is not None and feed.get("vix_chg") is None:
            vix_ltp = float(feed["india_vix"])
            session_vix = await cache.get_session_value(tenant_id, _session_dated_key("vix_session_open"))
            if session_vix is None:
                await cache.set_session_value(tenant_id, _session_dated_key("vix_session_open"), vix_ltp)
                session_vix = vix_ltp
            feed["vix_chg"] = round(vix_ltp - float(session_vix), 3)

        # Index / stock / USD-INR — medium tier batch
        aux_cached = await _cache_get(tenant_id, "aux_quotes")
        if isinstance(aux_cached, dict):
            feed.update(aux_cached)
        else:
            aux_symbols = list(
                dict.fromkeys(
                    [
                        *INDEX_KITE_SYMBOLS.values(),
                        *STOCK_KITE_SYMBOLS.values(),
                        USD_INR_KITE_SYMBOL,
                    ]
                )
            )
            aux_quotes = await self._fetch_quote(aux_symbols) if aux_symbols else {}
            aux_payload: dict[str, Any] = {}
            _apply_quote_pct_map(feed, aux_quotes, INDEX_KITE_SYMBOLS)
            _apply_quote_pct_map(feed, aux_quotes, STOCK_KITE_SYMBOLS)
            for key, val in feed.items():
                if key.startswith(("index_", "stock_")):
                    aux_payload[key] = val
            usd_row = _find_quote_row(aux_quotes, USD_INR_KITE_SYMBOL)
            usd_ltp = _pick_float(usd_row or {}, "last_price", "ltp", "last")
            if usd_ltp is not None:
                feed["usd_inr"] = usd_ltp
                aux_payload["usd_inr"] = usd_ltp
            sensex_row = _find_quote_row(aux_quotes, INDEX_KITE_SYMBOLS["index_sensex_chg"])
            if sensex_row:
                sensex_ltp = _pick_float(sensex_row, "last_price", "ltp", "last")
                ohlc = sensex_row.get("ohlc") if isinstance(sensex_row.get("ohlc"), dict) else {}
                sensex_open = _pick_float(ohlc, "open") if ohlc else None
                if sensex_ltp is not None and sensex_open is not None:
                    feed["sensex_points_move"] = round(sensex_ltp - sensex_open, 2)
                    aux_payload["sensex_points_move"] = feed["sensex_points_move"]
            if aux_payload:
                feed.update(aux_payload)
                await _cache_set(tenant_id, "aux_quotes", "medium", aux_payload)

        await _merge_yahoo_slow_tier(tenant_id, feed, mock=False)
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
                config, feed, has_broker=has_broker, team_ready=team_ready
            )
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
            config, feed, has_broker=has_broker, team_ready=team_ready
        )
        payload["underlying"] = {
            "symbol": config.underlying_symbol,
            "label": config.underlying_label or config.underlying_symbol or "—",
        }
        payload["broker_poll_ms"] = BROKER_QUOTE_TTL_MS
        payload["stream"] = True
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
