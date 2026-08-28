"""Admin Param Chart — monthly OHLC + fixed-strike premium trail via Kite."""

from __future__ import annotations

import calendar
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.domains import param_chart_cache as pc_cache
from app.domains import param_chart_metrics_store as metrics_store
from app.domains import signal_engine_cache as signal_cache
from app.domains.options_lab import suggest_fut_symbol
from app.domains.param_chart_constants import (
    DEFAULT_PARAM_CHART_CONFIG,
    PARAM_CHART_INTERVALS,
    PARAM_CHART_SETTINGS_KEY,
    merge_desk_instrument_into_chart,
    project_metrics_from_signal_rows,
    shared_categories,
    shared_metric_defs,
)
from app.domains.signal_engine import (
    UNDERLYING_PRESETS,
    SignalEngineService,
    _extract_candle_rows,
    _find_quote_row,
)
from app.domains.signal_engine_constants import STREAM_INTERVAL_MS

logger = get_logger(__name__)
IST = ZoneInfo("Asia/Kolkata")
MONTH_CODES = (
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
)


def _nse_pre_open(now: datetime | None = None) -> bool:
    """True before regular NSE cash session open (09:15 IST). Local to Param Chart."""
    now = now or datetime.now(IST)
    return (now.hour, now.minute) < (9, 15)


def _quote_ltp(
    row: dict[str, Any] | None,
    *,
    allow_previous_close: bool = False,
) -> tuple[float | None, bool]:
    """``(price, is_stale)`` — live last_price, else previous close when pre-open."""
    if not row:
        return None, False
    for key in ("last_price", "ltp", "last"):
        val = row.get(key)
        if val is None or val == "":
            continue
        try:
            live = float(val)
        except (TypeError, ValueError):
            continue
        if live != 0:
            return live, False
    if not allow_previous_close:
        return None, False
    ohlc = row.get("ohlc") if isinstance(row.get("ohlc"), dict) else {}
    close = ohlc.get("close") if isinstance(ohlc, dict) else None
    if close is None or close == "":
        return None, False
    try:
        return float(close), True
    except (TypeError, ValueError):
        return None, False


def _tenant_key(context: Any) -> str:
    return str(getattr(context, "tenant_id", "") or "")


def _ist_today() -> date:
    return datetime.now(IST).date()


def _pick_float(row: dict[str, Any] | None, *keys: str) -> float | None:
    if not row:
        return None
    for key in keys:
        val = row.get(key)
        if val is None or val == "":
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return None


def _token_from_quote_row(row: dict[str, Any] | None) -> int:
    if not row:
        return 0
    raw = row.get("instrument_token")
    try:
        return int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


def _option_root(underlying_symbol: str) -> tuple[str, str] | None:
    symbol = underlying_symbol.strip()
    mapping = {
        "NSE:NIFTY BANK": ("NFO", "BANKNIFTY"),
        "NSE:BANKNIFTY": ("NFO", "BANKNIFTY"),
        "NSE:NIFTY 50": ("NFO", "NIFTY"),
        "NSE:NIFTY": ("NFO", "NIFTY"),
        "NSE:NIFTY FIN SERVICE": ("NFO", "FINNIFTY"),
        "NSE:NIFTY MID SELECT": ("NFO", "MIDCPNIFTY"),
        "BSE:SENSEX": ("BFO", "SENSEX"),
    }
    return mapping.get(symbol)


def suggest_option_symbols(
    underlying_symbol: str,
    strike: int,
    *,
    when: datetime | None = None,
) -> tuple[str, str]:
    meta = _option_root(underlying_symbol)
    now = when or datetime.now(IST)
    year, month = now.year, now.month
    yy = str(year)[-2:]
    mon = MONTH_CODES[month - 1]
    if meta:
        exchange, root = meta
        return (
            f"{exchange}:{root}{yy}{mon}{strike}CE",
            f"{exchange}:{root}{yy}{mon}{strike}PE",
        )
    bare = underlying_symbol.split(":")[-1].replace(" ", "")
    return (
        f"NFO:{bare}{yy}{mon}{strike}CE",
        f"NFO:{bare}{yy}{mon}{strike}PE",
    )


def _option_expiry_ym(symbol: str) -> tuple[int, int] | None:
    """Parse ``NFO:NIFTY26JUL24000CE`` → (2026, 7)."""
    import re

    body = str(symbol or "").strip().upper().split(":", 1)[-1]
    match = re.search(r"(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)", body)
    if not match:
        return None
    yy = int(match.group(1))
    mon = MONTH_CODES.index(match.group(2)) + 1
    year = 2000 + yy if yy < 80 else 1900 + yy
    return year, mon


def _option_matches_chart_month(symbol: str, year: int, month: int) -> bool:
    parsed = _option_expiry_ym(symbol)
    if parsed is None:
        return False
    return parsed == (year, month)


# Minute / 5m hist payloads are large; sandbox/proxy frames fail on a full month.
# Fetch in short calendar windows and merge (dump-once still applies).
_INTRADAY_CHUNK_DAYS = {"1m": 2, "5m": 7}


def _iter_date_chunks(
    start: date, end: date, *, chunk_days: int
) -> list[tuple[date, date]]:
    """Inclusive calendar chunks of at most ``chunk_days`` days."""
    if end < start or chunk_days < 1:
        return []
    out: list[tuple[date, date]] = []
    cur = start
    step = timedelta(days=chunk_days)
    while cur <= end:
        chunk_end = min(cur + step - timedelta(days=1), end)
        out.append((cur, chunk_end))
        cur = chunk_end + timedelta(days=1)
    return out


def _merge_kite_hist_chunks(parts: list[Any]) -> dict[str, Any] | None:
    """Dedupe + sort candle rows from chunked ``get_historical_candles`` calls."""
    candles: list[Any] = []
    seen: set[str] = set()
    for part in parts:
        for row in _extract_candle_rows(part):
            if isinstance(row, (list, tuple)) and row:
                key = str(row[0])
            elif isinstance(row, dict):
                key = str(
                    row.get("date") or row.get("time") or row.get("timestamp") or ""
                )
            else:
                continue
            if not key or key in seen:
                continue
            seen.add(key)
            candles.append(row)
    if not candles:
        return None

    def _sort_key(row: Any) -> str:
        if isinstance(row, (list, tuple)) and row:
            return str(row[0])
        if isinstance(row, dict):
            return str(row.get("date") or row.get("time") or row.get("timestamp") or "")
        return ""

    candles.sort(key=_sort_key)
    return {"ok": True, "data": {"candles": candles}}


def _today_bar_indices(days: list[Any], today_s: str) -> list[int]:
    """Indices whose ``date`` is today (``YYYY-MM-DD`` or ``YYYY-MM-DDTHH:MM…``)."""
    return [
        i
        for i, d in enumerate(days)
        if isinstance(d, dict) and str(d.get("date") or "").startswith(today_s)
    ]


def _candle_ohlc_by_day(hist: Any) -> dict[str, dict[str, float]]:
    """Parse Kite candles → day-keyed OHLC (YYYY-MM-DD)."""
    return {
        k[:10]: v
        for k, v in _candle_ohlc_by_bucket(hist, grain="day").items()
    }


def _candle_ohlc_by_bucket(
    hist: Any, *, grain: str = "day"
) -> dict[str, dict[str, float]]:
    """Parse Kite candles → bucketed OHLC.

    grain:
      - ``day`` → YYYY-MM-DD
      - ``hour`` → YYYY-MM-DDTHH:00 (hour floor, IST wall clock from candle ts)
      - ``raw`` → full timestamp prefix (minute/hour as returned)
    """
    by_key: dict[str, dict[str, float]] = {}
    for c in _extract_candle_rows(hist):
        ts_raw = ""
        o = h = lo = cl = None
        vol = None
        if isinstance(c, (list, tuple)) and len(c) >= 5:
            ts_raw = str(c[0])
            try:
                o, h, lo, cl = float(c[1]), float(c[2]), float(c[3]), float(c[4])
                if len(c) >= 6 and c[5] is not None:
                    vol = float(c[5])
            except (TypeError, ValueError):
                continue
        elif isinstance(c, dict):
            ts_raw = str(c.get("date") or c.get("time") or c.get("timestamp") or "")
            try:
                o = float(c.get("open") or 0)
                h = float(c.get("high") or 0)
                lo = float(c.get("low") or 0)
                cl = float(c.get("close") or 0)
                if c.get("volume") not in (None, ""):
                    vol = float(c.get("volume"))
            except (TypeError, ValueError):
                continue
        if not ts_raw or cl is None:
            continue
        if grain == "day":
            key = ts_raw[:10]
        elif grain == "hour":
            # "2026-08-25T10:15:00+0530" → "2026-08-25T10:00"
            body = ts_raw.replace(" ", "T")
            key = body[:13] + ":00" if len(body) >= 13 else body[:10]
        else:
            key = ts_raw[:16].replace(" ", "T")
        row: dict[str, float] = {"open": o or 0, "high": h or 0, "low": lo or 0, "close": cl}
        if vol is not None:
            row["volume"] = vol
        by_key[key] = row
    return by_key


def _aggregate_monthly(
    daily: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    """Collapse daily OHLC → one bar per YYYY-MM."""
    months: dict[str, list[tuple[str, dict[str, float]]]] = {}
    for ds, vals in sorted(daily.items()):
        ym = ds[:7]
        months.setdefault(ym, []).append((ds, vals))
    out: dict[str, dict[str, float]] = {}
    for ym, rows in months.items():
        rows.sort(key=lambda x: x[0])
        opens = [r[1]["open"] for r in rows]
        highs = [r[1]["high"] for r in rows]
        lows = [r[1]["low"] for r in rows]
        closes = [r[1]["close"] for r in rows]
        vols = [r[1]["volume"] for r in rows if r[1].get("volume") is not None]
        bar: dict[str, float] = {
            "open": opens[0],
            "high": max(highs),
            "low": min(lows),
            "close": closes[-1],
        }
        if vols:
            bar["volume"] = float(sum(vols))
        out[ym] = bar
    return out


def _aggregate_weekly(
    daily: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    """Collapse daily OHLC → one bar per ISO week (key = Monday YYYY-MM-DD)."""
    weeks: dict[str, list[tuple[str, dict[str, float]]]] = {}
    for ds, vals in sorted(daily.items()):
        try:
            d = date.fromisoformat(ds[:10])
        except ValueError:
            continue
        monday = d - timedelta(days=d.weekday())
        key = monday.isoformat()
        weeks.setdefault(key, []).append((ds, vals))
    out: dict[str, dict[str, float]] = {}
    for key, rows in weeks.items():
        rows.sort(key=lambda x: x[0])
        opens = [r[1]["open"] for r in rows]
        highs = [r[1]["high"] for r in rows]
        lows = [r[1]["low"] for r in rows]
        closes = [r[1]["close"] for r in rows]
        vols = [r[1]["volume"] for r in rows if r[1].get("volume") is not None]
        bar: dict[str, float] = {
            "open": opens[0],
            "high": max(highs),
            "low": min(lows),
            "close": closes[-1],
        }
        if vols:
            bar["volume"] = float(sum(vols))
        out[key] = bar
    return out


def _normalize_interval(raw: Any) -> str:
    from app.domains.param_chart_constants import normalize_param_chart_interval

    return normalize_param_chart_interval(raw)


def _kite_interval_for(ui_interval: str) -> str:
    for row in PARAM_CHART_INTERVALS:
        if row["id"] == ui_interval:
            return row["kite"]
    return "day"


def _candle_close_by_day(hist: Any) -> dict[str, float]:
    return {
        ds: vals["close"]
        for ds, vals in _candle_ohlc_by_day(hist).items()
        if vals.get("close") is not None
    }


def _attach_day_deltas(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add close day-over-day difference for the bottom histogram pane."""
    out: list[dict[str, Any]] = []
    prev_close: float | None = None
    for row in days:
        updated = dict(row)
        close = updated.get("close")
        try:
            close_f = float(close) if close is not None else None
        except (TypeError, ValueError):
            close_f = None
        if close_f is not None and prev_close is not None:
            updated["chg"] = round(close_f - prev_close, 2)
        else:
            updated["chg"] = None
        if close_f is not None:
            prev_close = close_f
        out.append(updated)
    return out


def _empty_day(day: date, *, day_index: int) -> dict[str, Any]:
    return {
        "date": day.isoformat(),
        "day_index": day_index,
        "weekday": day.strftime("%a"),
        "open": None,
        "high": None,
        "low": None,
        "close": None,
        "volume": None,
        "chg": None,
        "ce": None,
        "pe": None,
        "total": None,
        "pct_vs_entry": None,
        "metrics": {},
        "is_today": day == _ist_today(),
    }


def _trading_days_in_month(
    year: int,
    month: int,
    *,
    holidays: frozenset[date] | None = None,
) -> list[date]:
    from app.domains.options_lab_bots import nse_holidays_effective

    hol = holidays if holidays is not None else nse_holidays_effective()
    _, last = calendar.monthrange(year, month)
    out: list[date] = []
    for d in range(1, last + 1):
        day = date(year, month, d)
        if day.weekday() >= 5:
            continue
        if day in hol:
            continue
        out.append(day)
    return out


def _parse_float(val: Any, default: float) -> float:
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _apply_premiums(
    row: dict[str, Any],
    *,
    ce: float | None,
    pe: float | None,
    entry_total: float,
) -> dict[str, Any]:
    updated = dict(row)
    if ce is not None:
        updated["ce"] = round(ce, 2)
    if pe is not None:
        updated["pe"] = round(pe, 2)
    if updated.get("ce") is not None and updated.get("pe") is not None:
        total = round(float(updated["ce"]) + float(updated["pe"]), 2)
        updated["total"] = total
        if entry_total:
            updated["pct_vs_entry"] = round((entry_total - total) / entry_total * 100, 2)
    return updated


@dataclass
class ParamChartConfig:
    underlying_symbol: str = "NSE:NIFTY BANK"
    underlying_label: str = "BANKNIFTY"
    fut_symbol: str = ""
    strike_step: int = 100
    strike: int | None = None
    entry_ce_premium: float = 900.0
    entry_pe_premium: float = 900.0
    ce_symbol: str = ""
    pe_symbol: str = ""
    year: int | None = None
    month: int | None = None
    interval: str = "1D"

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> ParamChartConfig:
        data = {**DEFAULT_PARAM_CHART_CONFIG, **(raw or {})}
        strike = data.get("strike")
        try:
            strike_i = int(strike) if strike not in (None, "") else None
        except (TypeError, ValueError):
            strike_i = None
        year = data.get("year")
        month = data.get("month")
        try:
            year_i = int(year) if year not in (None, "") else None
            month_i = int(month) if month not in (None, "") else None
        except (TypeError, ValueError):
            year_i, month_i = None, None
        return cls(
            underlying_symbol=str(data.get("underlying_symbol") or "NSE:NIFTY BANK"),
            underlying_label=str(data.get("underlying_label") or "BANKNIFTY"),
            fut_symbol=str(data.get("fut_symbol") or ""),
            strike_step=int(data.get("strike_step") or 100),
            strike=strike_i,
            entry_ce_premium=_parse_float(data.get("entry_ce_premium"), 900.0),
            entry_pe_premium=_parse_float(data.get("entry_pe_premium"), 900.0),
            ce_symbol=str(data.get("ce_symbol") or ""),
            pe_symbol=str(data.get("pe_symbol") or ""),
            year=year_i,
            month=month_i,
            interval=_normalize_interval(data.get("interval")),
        )

    def to_admin_dict(self) -> dict[str, Any]:
        today = _ist_today()
        return {
            "underlying_symbol": self.underlying_symbol,
            "underlying_label": self.underlying_label,
            "fut_symbol": self.fut_symbol,
            "strike_step": self.strike_step,
            "strike": self.strike,
            "entry_ce_premium": self.entry_ce_premium,
            "entry_pe_premium": self.entry_pe_premium,
            "ce_symbol": self.ce_symbol,
            "pe_symbol": self.pe_symbol,
            "year": self.year or today.year,
            "month": self.month or today.month,
            "interval": _normalize_interval(self.interval),
        }

    def entry_total(self) -> float:
        return float(self.entry_ce_premium) + float(self.entry_pe_premium)

    def resolved_year_month(self) -> tuple[int, int]:
        today = _ist_today()
        return (self.year or today.year, self.month or today.month)


def heal_option_symbols_for_month(
    cfg: ParamChartConfig, *, year: int, month: int
) -> ParamChartConfig:
    """Roll CE/PE to the chart month's monthly expiry when stale/missing.

    In-memory only — SSE/overlay must not persist on the live tick.
    """
    strike = int(cfg.strike or 0)
    if strike <= 0:
        return cfg
    ce_ok = bool(cfg.ce_symbol) and _option_matches_chart_month(
        cfg.ce_symbol, year, month
    )
    pe_ok = bool(cfg.pe_symbol) and _option_matches_chart_month(
        cfg.pe_symbol, year, month
    )
    if ce_ok and pe_ok:
        return cfg
    when = datetime(year, month, 15, tzinfo=IST)
    ce, pe = suggest_option_symbols(cfg.underlying_symbol, strike, when=when)
    if ce == cfg.ce_symbol and pe == cfg.pe_symbol:
        return cfg
    return ParamChartConfig.from_dict(
        {
            **cfg.to_admin_dict(),
            "ce_symbol": ce,
            "pe_symbol": pe,
            "year": year,
            "month": month,
        }
    )


async def config_from_setup_cache(tenant_id: str) -> ParamChartConfig | None:
    """Param Chart config from the Signal setup memo. ``None`` on a cold miss."""
    hit = await signal_cache.get_metric(tenant_id, "setup")
    if hit is None:
        return None
    settings = hit.get("settings") if isinstance(hit, dict) else None
    if not isinstance(settings, dict):
        return ParamChartConfig()
    merged = merge_desk_instrument_into_chart(settings)
    return ParamChartConfig.from_dict(merged)


async def _book_quotes(tenant_id: str, symbols: list[str]) -> dict[str, Any]:
    """Ticker book only — never REST. Empty dict if the book has nothing."""
    if not symbols:
        return {}
    from app.domains.kite_ticker_hub import assemble_quotes_from_book

    live = await assemble_quotes_from_book(
        tenant_id, symbols, require_all=False, require_alive=True
    )
    if live:
        return live
    soft = await assemble_quotes_from_book(
        tenant_id, symbols, require_all=False, require_alive=False
    )
    return soft or {}


async def live_quote_overlay(tenant_id: str, cfg: ParamChartConfig) -> dict[str, Any]:
    """Book-first spot/CE/PE + Signal shared metrics (no hist, no REST)."""
    metrics: dict[str, Any] = {}
    snap = await signal_cache.get_snapshot(tenant_id)
    if isinstance(snap, dict):
        metrics = project_metrics_from_signal_rows(snap.get("metrics"))

    ce = pe = total = pct = None
    spot_close = spot_open = spot_high = spot_low = None
    quote_error: str | None = None
    quote_stale = False
    allow_stale = _nse_pre_open()
    try:
        symbols = [
            s for s in (cfg.underlying_symbol, cfg.ce_symbol, cfg.pe_symbol) if s
        ]
        quotes = await _book_quotes(tenant_id, symbols)
        spot_row = _find_quote_row(quotes, cfg.underlying_symbol)
        spot_close, spot_stale = _quote_ltp(
            spot_row,
            allow_previous_close=allow_stale,
        )
        if spot_stale:
            quote_stale = True
        ohlc = spot_row.get("ohlc") if isinstance(spot_row, dict) else None
        if isinstance(ohlc, dict):
            spot_open = _pick_float(ohlc, "open")
            spot_high = _pick_float(ohlc, "high")
            spot_low = _pick_float(ohlc, "low")
        ce_ltp, ce_stale = _quote_ltp(
            _find_quote_row(quotes, cfg.ce_symbol),
            allow_previous_close=allow_stale,
        )
        pe_ltp, pe_stale = _quote_ltp(
            _find_quote_row(quotes, cfg.pe_symbol),
            allow_previous_close=allow_stale,
        )
        ce, pe = ce_ltp, pe_ltp
        if ce_stale or pe_stale:
            quote_stale = True
        if ce is not None and pe is not None:
            total = round(ce + pe, 2)
            entry = cfg.entry_total()
            if entry:
                pct = round((entry - total) / entry * 100, 2)
        if not quotes and symbols:
            quote_error = "kite_quote_empty"
    except Exception as exc:  # noqa: BLE001
        quote_error = str(exc)[:200]
        logger.warning("param_chart_today_quote_failed", tenant_id=tenant_id)

    kite_live: dict[str, Any] = {
        "source": "book",
        "spot": spot_close,
        "ce": ce,
        "pe": pe,
        "error": quote_error,
    }
    if quote_stale:
        kite_live["quote_stale"] = True
        kite_live["quote_reference"] = "previous_close"
    return {
        "live_metrics": metrics,
        "kite_live": kite_live,
        "quote_stale": quote_stale,
        "quote_reference": "previous_close" if quote_stale else None,
        "spot_close": spot_close,
        "spot_open": spot_open,
        "spot_high": spot_high,
        "spot_low": spot_low,
        "ce": ce,
        "pe": pe,
        "total": total,
        "pct": pct,
    }


def apply_today_overlay(
    cfg: ParamChartConfig,
    pack: dict[str, Any],
    live: dict[str, Any],
) -> dict[str, Any]:
    """Paint today's bar from a live overlay onto a cached month pack."""
    today = _ist_today()
    days = list(pack.get("days") or [])
    today_s = today.isoformat()
    today_idxs = _today_bar_indices(days, today_s)
    today_row = days[today_idxs[-1]] if today_idxs else None

    metrics = live["live_metrics"] if isinstance(live.get("live_metrics"), dict) else {}
    spot_close = live.get("spot_close")
    spot_open = live.get("spot_open")
    spot_high = live.get("spot_high")
    spot_low = live.get("spot_low")
    ce = live.get("ce")
    pe = live.get("pe")
    total = live.get("total")
    pct = live.get("pct")

    if today_row is not None and today_idxs:
        updated = dict(today_row)
        updated["is_today"] = True
        updated["metrics"] = {}
        if spot_open is not None:
            updated["open"] = spot_open
        if spot_high is not None:
            updated["high"] = spot_high
        if spot_low is not None:
            updated["low"] = spot_low
        if spot_close is not None:
            updated["close"] = spot_close
            if updated.get("open") is None:
                updated["open"] = spot_close
            if updated.get("high") is None:
                updated["high"] = spot_close
            if updated.get("low") is None:
                updated["low"] = spot_close
        if ce is not None or pe is not None:
            updated = _apply_premiums(
                updated, ce=ce, pe=pe, entry_total=cfg.entry_total()
            )
        elif total is not None:
            updated["total"] = total
            updated["pct_vs_entry"] = pct
        days = list(days)
        days[today_idxs[-1]] = updated
        for i in today_idxs[:-1]:
            row = dict(days[i])
            row["is_today"] = True
            row["metrics"] = {}
            days[i] = row
    elif today.weekday() < 5 and (
        pack.get("year") == today.year and pack.get("month") == today.month
    ):
        row = _empty_day(today, day_index=len(days) + 1)
        row["metrics"] = {}
        if spot_close is not None:
            row["open"] = spot_open or spot_close
            row["high"] = spot_high or spot_close
            row["low"] = spot_low or spot_close
            row["close"] = spot_close
        if ce is not None or pe is not None:
            row = _apply_premiums(
                row, ce=ce, pe=pe, entry_total=cfg.entry_total()
            )
        days = [*days, row]

    metrics_by_day = metrics_store.normalize_metrics_by_day(
        pack.get("metrics_by_day")
        if isinstance(pack.get("metrics_by_day"), dict)
        else {}
    )
    if metrics:
        prev_today = (
            metrics_by_day.get(today_s)
            if isinstance(metrics_by_day.get(today_s), dict)
            else {}
        )
        metrics_by_day[today_s] = {**prev_today, **metrics}

    kite_live = live.get("kite_live") if isinstance(live.get("kite_live"), dict) else {}
    lean_days = metrics_store.strip_embedded_metrics(_attach_day_deltas(days))
    out = {
        **pack,
        "days": lean_days,
        "metrics_by_day": metrics_by_day,
        "today": today.isoformat(),
        "live_metrics": metrics,
        "kite_live": kite_live,
        "fetched_at": int(time.time()),
        "stream_interval_ms": STREAM_INTERVAL_MS,
    }
    if live.get("quote_stale"):
        out["quote_stale"] = True
        out["quote_reference"] = live.get("quote_reference") or "previous_close"
    return out


async def refresh_overlay_from_cache(tenant_id: str) -> dict[str, Any] | None:
    """Write today's SSE overlay from Redis + ticker book. No Postgres / Kite hist."""
    await pc_cache.touch_watcher(tenant_id)
    cfg = await config_from_setup_cache(tenant_id)
    if cfg is None:
        return None
    y, m = cfg.resolved_year_month()
    cfg = heal_option_symbols_for_month(cfg, year=y, month=m)
    pack = await pc_cache.get_month_pack(
        tenant_id, year=y, month=m, interval=cfg.interval
    )
    live = await live_quote_overlay(tenant_id, cfg)
    if isinstance(pack, dict) and pack.get("days"):
        merged = apply_today_overlay(cfg, pack, live)
    else:
        # Pack miss: live quotes only. Never stamp building=True on the overlay —
        # that froze SSE (no fall-through) and the desk spinner. Hist stays on
        # GET /month; empty days let the client keep a REST-loaded series.
        today_s = _ist_today().isoformat()
        live_metrics = (
            live["live_metrics"] if isinstance(live.get("live_metrics"), dict) else {}
        )
        merged = {
            "ok": True,
            "building": False,
            "year": y,
            "month": m,
            "interval": cfg.interval,
            "today": today_s,
            "days": [],
            "metrics_by_day": {today_s: live_metrics} if live_metrics else {},
            "live_metrics": live_metrics,
            "kite_live": live.get("kite_live") or {"source": "book", "error": None},
            "fetched_at": int(time.time()),
            "stream_interval_ms": STREAM_INTERVAL_MS,
            "config": cfg.to_admin_dict(),
        }
        if live.get("quote_stale"):
            merged["quote_stale"] = True
            merged["quote_reference"] = live.get("quote_reference") or "previous_close"
    slim = _slim_stream_frame(merged)
    await pc_cache.set_overlay(tenant_id, slim)
    return slim


class ParamChartService:
    """Param Chart data — Kite only (quotes + historical candles)."""

    def __init__(self, session: AsyncSession, context: Any) -> None:
        self.session = session
        self.context = context
        self.engine = SignalEngineService(session, context)

    async def _read_config(self) -> ParamChartConfig:
        """Param Chart nested settings from the shared signal ``setup`` Redis memo.

        Same blob ``SignalEngineService._load_setup`` uses — avoids a tool/
        settings DB round-trip on every metrics persist tick.
        """
        tenant_id = _tenant_key(self.context)
        hit = await signal_cache.get_metric(tenant_id, "setup")
        if hit is None:
            await self.engine._load_setup()
            hit = await signal_cache.get_metric(tenant_id, "setup")
        settings = hit.get("settings") if isinstance(hit, dict) else None
        if isinstance(settings, dict):
            merged = merge_desk_instrument_into_chart(settings)
            if merged:
                return ParamChartConfig.from_dict(merged)
            return ParamChartConfig()
        # Setup miss / empty — fall back to a direct tool read.
        tool = await self.engine._signal_engine_tool()
        if tool is None:
            return ParamChartConfig()
        raw = await self.engine._tool_settings(tool)
        merged = merge_desk_instrument_into_chart(raw)
        if merged:
            return ParamChartConfig.from_dict(merged)
        return ParamChartConfig()

    async def get_admin_config(self) -> dict[str, Any]:
        tool = await self.engine._signal_engine_tool()
        _, has_broker, team_ready = await self.engine._load_setup()
        cfg = await self._read_config()
        return {
            "ok": True,
            "config": cfg.to_admin_dict(),
            "presets": list(UNDERLYING_PRESETS),
            "shared_metrics": shared_metric_defs(),
            "shared_categories": shared_categories(),
            "intervals": list(PARAM_CHART_INTERVALS),
            "tool_bound": tool is not None,
            "has_broker": has_broker,
            "team_ready": team_ready,
            "data_source": "kite",
        }

    async def update_admin_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        tool = await self.engine._signal_engine_tool()
        if tool is None:
            return {
                "ok": False,
                "error": "Signal engine tool not bound on Signals ops team.",
            }
        current = await self.engine._tool_settings(tool)
        chart = ParamChartConfig.from_dict(
            current.get(PARAM_CHART_SETTINGS_KEY)
            if isinstance(current.get(PARAM_CHART_SETTINGS_KEY), dict)
            else None,
        )
        merged = chart.to_admin_dict()
        # Drop legacy mock if present in stored settings.
        merged.pop("mock", None)
        for key in (
            "underlying_symbol",
            "underlying_label",
            "fut_symbol",
            "strike_step",
            "strike",
            "entry_ce_premium",
            "entry_pe_premium",
            "ce_symbol",
            "pe_symbol",
            "year",
            "month",
            "interval",
        ):
            if key not in patch:
                continue
            val = patch[key]
            if val is None or val == "":
                if key in ("strike", "year", "month"):
                    merged[key] = None
                else:
                    merged[key] = DEFAULT_PARAM_CHART_CONFIG.get(key, "")
            else:
                merged[key] = val

        if patch.get("underlying_symbol"):
            symbol = str(merged["underlying_symbol"])
            match = next(
                (p for p in UNDERLYING_PRESETS if p.get("symbol") == symbol),
                None,
            )
            if match:
                merged["underlying_label"] = match.get("label") or merged["underlying_label"]
                merged["strike_step"] = int(match.get("strike_step") or merged["strike_step"])
            if not patch.get("fut_symbol"):
                merged["fut_symbol"] = suggest_fut_symbol(symbol)
            if "ce_symbol" not in patch:
                merged["ce_symbol"] = ""
            if "pe_symbol" not in patch:
                merged["pe_symbol"] = ""
            # Stale BankNifty 57000 (etc.) must not rederive NIFTY…57000CE.
            if "strike" not in patch:
                merged["strike"] = None

        year = int(merged.get("year") or _ist_today().year)
        month = int(merged.get("month") or _ist_today().month)
        merged["interval"] = _normalize_interval(merged.get("interval"))
        interval = str(merged["interval"])
        step = int(merged.get("strike_step") or 100)
        strike = merged.get("strike")
        if strike in (None, ""):
            label = str(merged.get("underlying_label") or "").upper()
            strike = (
                int(round(57_000 / step) * step)
                if "BANK" in label
                else int(round(24_000 / step) * step)
            )
        strike = int(strike)
        merged["strike"] = strike

        # Re-derive fixed-strike contracts when setup keys change (unless caller
        # explicitly set ce_symbol / pe_symbol in this patch).
        rederive_opts = (
            "strike" in patch
            or "year" in patch
            or "month" in patch
            or "underlying_symbol" in patch
            or not merged.get("ce_symbol")
            or not merged.get("pe_symbol")
        )
        if rederive_opts and "ce_symbol" not in patch and "pe_symbol" not in patch:
            when = datetime(year, month, 15, tzinfo=IST)
            ce, pe = suggest_option_symbols(
                str(merged["underlying_symbol"]),
                strike,
                when=when,
            )
            merged["ce_symbol"] = ce
            merged["pe_symbol"] = pe
        if not merged.get("fut_symbol"):
            merged["fut_symbol"] = suggest_fut_symbol(str(merged["underlying_symbol"]))

        from app.domains.desk_instrument import (
            board_from_mapping,
            desk_instrument_tool_patch,
            patch_touches_identity,
        )

        tool_patch: dict[str, Any] = {PARAM_CHART_SETTINGS_KEY: merged}
        if patch_touches_identity(patch):
            board = board_from_mapping(
                merged,
                source="param-chart",
                atm=merged.get("strike"),
            )
            desk_patch = desk_instrument_tool_patch(board, current)
            if desk_patch:
                tool_patch.update(desk_patch)
        # Patch only our nested subtree — never rewrite Signal / Options Lab keys.
        await self.engine._patch_tool_settings(tool, tool_patch)
        # Nested param_chart lives inside the signal setup memo — drop it so the
        # next _read_config / _load_setup sees the patch (Fix 4 / memo path).
        await signal_cache.delete_metric(_tenant_key(self.context), "setup")
        # Interval packs are keyed separately. Switching 5m ↔ 15m must not
        # drop the destination Redis pack or the UI waits on a rebuild.
        # Strike / month / underlying still invalidate the pack we are about
        # to load (dump on disk is reused; Redis is the hot path).
        candle_identity = (
            "year",
            "month",
            "strike",
            "underlying_symbol",
            "fut_symbol",
            "ce_symbol",
            "pe_symbol",
        )
        if any(k in patch for k in candle_identity):
            tenant_id = _tenant_key(self.context)
            # Strike / month / underlying invalidate every interval pack for
            # this period — interval is keyed separately but shares OHLC body.
            await pc_cache.delete_month_packs_for_period(
                tenant_id, year=year, month=month
            )
        return {"ok": True, **await self.get_admin_config()}

    async def _kite_hist(
        self,
        token: int,
        *,
        year: int,
        month: int,
        interval: str = "1D",
    ) -> Any | None:
        """Candles from OCI/S3 dump first, else Kite, then persist dump."""
        if token <= 0:
            return None
        from app.domains import param_chart_candle_store as candle_store

        ui_iv = _normalize_interval(interval)
        kite_iv = _kite_interval_for(ui_iv)
        stored = await candle_store.get_month_candles(
            token, year=year, month=month, interval=ui_iv
        )
        refresh_month = (
            month
            if ui_iv not in ("1M", "1W")
            else date(_ist_today().year, _ist_today().month, 1).month
        )
        refresh_year = year
        if stored is not None and not candle_store.should_refresh_month_dump(
            stored, year=refresh_year, month=refresh_month
        ):
            return stored

        if ui_iv in ("1M", "1W"):
            start = date(year, 1, 1)
            end = date(year, 12, 31)
        else:
            start = date(year, month, 1)
            _, last = calendar.monthrange(year, month)
            end = date(year, month, last)

        today = _ist_today()
        if end > today:
            end = today
        if start > end:
            return stored

        hist: Any | None = None
        minute_chunk_failures = 0
        chunk_days = _INTRADAY_CHUNK_DAYS.get(ui_iv)
        if chunk_days:
            parts: list[Any] = []
            chunks = _iter_date_chunks(start, end, chunk_days=chunk_days)
            for a, b in chunks:
                part = await self.engine._invoke_broker_tool(
                    "get_historical_candles",
                    {
                        "instrument_token": token,
                        "interval": kite_iv,
                        "from_date": f"{a.isoformat()} 09:15:00",
                        "to_date": f"{b.isoformat()} 15:30:00",
                    },
                )
                # Treat missing / explicit failure as a hole — do not cold-dump
                # a partial month (past months would never refresh).
                if part is None or (
                    isinstance(part, dict) and part.get("ok") is False
                ):
                    minute_chunk_failures += 1
                    continue
                parts.append(part)
            hist = _merge_kite_hist_chunks(parts)
            if minute_chunk_failures:
                merged_n = 0
                if isinstance(hist, dict):
                    data = hist.get("data")
                    if isinstance(data, dict) and isinstance(data.get("candles"), list):
                        merged_n = len(data["candles"])
                logger.warning(
                    "param_chart_intraday_hist_chunk_failed",
                    token=token,
                    year=year,
                    month=month,
                    interval=ui_iv,
                    chunks=len(chunks),
                    failed=minute_chunk_failures,
                    merged=merged_n,
                )
        else:
            hist = await self.engine._invoke_broker_tool(
                "get_historical_candles",
                {
                    "instrument_token": token,
                    "interval": kite_iv,
                    "from_date": f"{start.isoformat()} 09:15:00",
                    "to_date": f"{end.isoformat()} 15:30:00",
                },
            )
        if hist is not None:
            # Only persist complete intraday merges; partial packs stay ephemeral.
            if not (chunk_days and minute_chunk_failures):
                uri = await candle_store.put_month_candles(
                    token,
                    year=year,
                    month=month,
                    hist=hist,
                    source="kite",
                    interval=ui_iv,
                )
                if uri:
                    logger.info(
                        "param_chart_candle_dumped",
                        token=token,
                        year=year,
                        month=month,
                        interval=ui_iv,
                        uri=uri[:120],
                    )
            return hist
        return stored

    async def _token_from_instruments(self, symbol: str) -> int:
        """Fallback: Kite get_instruments via desk/trading team bindings."""
        if not symbol:
            return 0
        from app.domains.desk_snapshot import DESK_TEAM_SLUGS, invoke_tool
        from app.domains.options_lab_trading import OptionsLabTradingService
        from app.domains.options_lab_underlyings import (
            extract_instruments_csv,
            extract_instruments_rows,
        )
        from app.domains.signal_engine import SIGNAL_TEAM_SLUG

        exchange = symbol.split(":", 1)[0] if ":" in symbol else "NFO"
        trading = OptionsLabTradingService(self.session, self.context)
        fn, _, _ = await trading._find_tool(
            ("get_instruments", "list_instruments"),
            team_slugs=(SIGNAL_TEAM_SLUG, *DESK_TEAM_SLUGS, "paper-trading", "live-trading"),
        )
        if fn is None:
            return 0
        try:
            raw = await invoke_tool(fn, {"exchange": exchange})
        except Exception:  # noqa: BLE001
            return 0

        want = symbol.split(":", 1)[-1].strip().upper()
        rows = extract_instruments_rows(raw) or []
        if not rows:
            csv_text = extract_instruments_csv(raw)
            if csv_text:
                import csv
                import io

                reader = csv.DictReader(io.StringIO(csv_text))
                rows = [dict(r) for r in reader]
        for row in rows:
            if not isinstance(row, dict):
                continue
            ts = str(
                row.get("tradingsymbol")
                or row.get("trading_symbol")
                or row.get("symbol")
                or ""
            ).strip().upper()
            if ts != want:
                continue
            try:
                return int(row.get("instrument_token") or 0)
            except (TypeError, ValueError):
                return 0
        return 0

    async def _kite_tokens(
        self, cfg: ParamChartConfig
    ) -> tuple[dict[str, Any], int, int, int]:
        """Resolve instrument_tokens via Kite get_quote, instruments, then cold map.

        Spot is quoted alone so expired/bad CE/PE symbols cannot block OHLC.
        CE/PE tokens are persisted while live so past months can still load hist.
        """
        from app.domains import param_chart_token_store as token_store

        quotes: dict[str, Any] = {}
        spot_tok = ce_tok = pe_tok = 0
        if cfg.underlying_symbol:
            spot_quotes = await self.engine._fetch_quote(
                [cfg.underlying_symbol], prefer="get_quote"
            )
            if isinstance(spot_quotes, dict):
                quotes.update(spot_quotes)
            spot_tok = _token_from_quote_row(
                _find_quote_row(quotes, cfg.underlying_symbol)
            )
            if not spot_tok:
                spot_tok = await self._token_from_instruments(cfg.underlying_symbol)
            if spot_tok:
                await token_store.put_instrument_token(cfg.underlying_symbol, spot_tok)

        opt_syms = [s for s in (cfg.ce_symbol, cfg.pe_symbol) if s]
        if opt_syms:
            try:
                opt_quotes = await self.engine._fetch_quote(
                    opt_syms, prefer="get_quote"
                )
                if isinstance(opt_quotes, dict):
                    quotes.update(opt_quotes)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "param_chart_option_quote_failed",
                    symbols=opt_syms[:4],
                )
            ce_tok = _token_from_quote_row(_find_quote_row(quotes, cfg.ce_symbol))
            pe_tok = _token_from_quote_row(_find_quote_row(quotes, cfg.pe_symbol))
            if not ce_tok and cfg.ce_symbol:
                ce_tok = await self._token_from_instruments(cfg.ce_symbol)
            if not pe_tok and cfg.pe_symbol:
                pe_tok = await self._token_from_instruments(cfg.pe_symbol)
            # Expired F&O: reuse tokens captured while the contract was live.
            if not ce_tok and cfg.ce_symbol:
                saved = await token_store.get_instrument_token(cfg.ce_symbol)
                if saved:
                    ce_tok = int(saved)
            if not pe_tok and cfg.pe_symbol:
                saved = await token_store.get_instrument_token(cfg.pe_symbol)
                if saved:
                    pe_tok = int(saved)
            if ce_tok and cfg.ce_symbol:
                await token_store.put_instrument_token(cfg.ce_symbol, ce_tok)
            if pe_tok and cfg.pe_symbol:
                await token_store.put_instrument_token(cfg.pe_symbol, pe_tok)
        return quotes, spot_tok, ce_tok, pe_tok

    async def _heal_option_symbols_for_month(
        self, cfg: ParamChartConfig, *, year: int, month: int
    ) -> ParamChartConfig:
        return heal_option_symbols_for_month(cfg, year=year, month=month)

    async def _backfill_from_kite(
        self,
        cfg: ParamChartConfig,
        days: list[dict[str, Any]],
        *,
        year: int,
        month: int,
        interval: str = "1D",
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Fill OHLC + CE/PE trail from Kite historical candles."""
        ui_iv = _normalize_interval(interval or cfg.interval)
        meta: dict[str, Any] = {
            "source": "kite",
            "interval": ui_iv,
            "spot_token": None,
            "ce_token": None,
            "pe_token": None,
            "ohlc_days": 0,
            "premium_days": 0,
            "errors": [],
        }
        try:
            _quotes, spot_tok, ce_tok, pe_tok = await self._kite_tokens(cfg)
        except Exception as exc:  # noqa: BLE001
            meta["errors"].append(f"quote:{exc!s}"[:200])
            return days, meta

        meta["spot_token"] = spot_tok or None
        meta["ce_token"] = ce_tok or None
        meta["pe_token"] = pe_tok or None

        spot_by_key: dict[str, dict[str, float]] = {}
        if spot_tok:
            hist = await self._kite_hist(
                spot_tok, year=year, month=month, interval=ui_iv
            )
            if hist is None:
                meta["errors"].append("spot_hist_unavailable")
            elif ui_iv in ("1m", "5m", "15m"):
                # Raw Kite intraday bars (key ≈ YYYY-MM-DDTHH:MM).
                spot_by_key = _candle_ohlc_by_bucket(hist, grain="raw")
            elif ui_iv == "1H":
                spot_by_key = _candle_ohlc_by_bucket(hist, grain="hour")
            elif ui_iv == "1W":
                spot_by_key = _aggregate_weekly(
                    _candle_ohlc_by_bucket(hist, grain="day")
                )
            elif ui_iv == "1M":
                spot_by_key = _aggregate_monthly(
                    _candle_ohlc_by_bucket(hist, grain="day")
                )
            else:
                spot_by_key = _candle_ohlc_by_bucket(hist, grain="day")
            meta["ohlc_days"] = len(spot_by_key)
        else:
            meta["errors"].append("spot_token_missing")

        # Premium trail stays daily (fixed-strike day closes) even on 1m/1H/1W/1M.
        ce_closes: dict[str, float] = {}
        pe_closes: dict[str, float] = {}
        if ce_tok:
            hist_ce = await self._kite_hist(
                ce_tok, year=year, month=month, interval="1D"
            )
            if hist_ce is None:
                meta["errors"].append("ce_hist_unavailable")
            else:
                ce_closes = _candle_close_by_day(hist_ce)
        elif cfg.ce_symbol:
            meta["errors"].append(
                "ce_token_missing: expired/unknown CE — open this month once "
                "while the contract is live (Refresh) to save its token"
            )
        else:
            meta["errors"].append("ce_symbol_empty")
        if pe_tok:
            hist_pe = await self._kite_hist(
                pe_tok, year=year, month=month, interval="1D"
            )
            if hist_pe is None:
                meta["errors"].append("pe_hist_unavailable")
            else:
                pe_closes = _candle_close_by_day(hist_pe)
        elif cfg.pe_symbol:
            meta["errors"].append(
                "pe_token_missing: expired/unknown PE — open this month once "
                "while the contract is live (Refresh) to save its token"
            )
        else:
            meta["errors"].append("pe_symbol_empty")

        entry_total = cfg.entry_total()

        # Non-daily resolutions: build bars from candle keys (no holiday skeleton).
        if ui_iv in ("1m", "5m", "15m", "1H", "1W", "1M") and spot_by_key:
            out: list[dict[str, Any]] = []
            for i, (key, ohlc) in enumerate(sorted(spot_by_key.items()), start=1):
                label_date = key[:10] if len(key) >= 10 else key
                try:
                    d0 = date.fromisoformat(label_date)
                    weekday = d0.strftime("%a")
                except ValueError:
                    weekday = ""
                row: dict[str, Any] = {
                    "date": key,
                    "day_index": i,
                    "weekday": weekday,
                    "open": ohlc.get("open"),
                    "high": ohlc.get("high"),
                    "low": ohlc.get("low"),
                    "close": ohlc.get("close"),
                    "volume": ohlc.get("volume"),
                    "chg": None,
                    "ce": None,
                    "pe": None,
                    "total": None,
                    "pct_vs_entry": None,
                    "metrics": {},
                    "is_today": label_date == _ist_today().isoformat(),
                }
                # Map premiums by calendar day for hourly/monthly bars.
                # Weekly: use last available CE/PE close in that Mon–Sun window.
                ce = ce_closes.get(label_date)
                pe = pe_closes.get(label_date)
                if ui_iv == "1W" and len(label_date) >= 10:
                    try:
                        week_start = date.fromisoformat(label_date[:10])
                        d = week_start + timedelta(days=6)
                        while d >= week_start:
                            ds = d.isoformat()
                            if ce is None and ds in ce_closes:
                                ce = ce_closes[ds]
                            if pe is None and ds in pe_closes:
                                pe = pe_closes[ds]
                            if ce is not None and pe is not None:
                                break
                            d -= timedelta(days=1)
                    except ValueError:
                        pass
                if ce is not None or pe is not None:
                    row = _apply_premiums(
                        row, ce=ce, pe=pe, entry_total=entry_total
                    )
                out.append(row)
            premium_hits = sum(1 for d in out if d.get("total") is not None)
            meta["premium_days"] = premium_hits
            return _attach_day_deltas(out), meta

        out = []
        premium_hits = 0
        for row in days:
            ds = row["date"]
            updated = dict(row)
            if ds in spot_by_key:
                updated.update(spot_by_key[ds])
            ce = ce_closes.get(ds)
            pe = pe_closes.get(ds)
            if ce is not None or pe is not None:
                updated = _apply_premiums(
                    updated, ce=ce, pe=pe, entry_total=entry_total
                )
                if updated.get("total") is not None:
                    premium_hits += 1
            out.append(updated)
        meta["premium_days"] = premium_hits
        return _attach_day_deltas(out), meta

    async def _ensure_month_skeleton(
        self,
        cfg: ParamChartConfig,
        *,
        year: int,
        month: int,
        force_refresh: bool = False,
        build_missing: bool = True,
    ) -> dict[str, Any]:
        tenant_id = _tenant_key(self.context)
        ui_iv = _normalize_interval(cfg.interval)
        cached = await pc_cache.get_month_pack(
            tenant_id, year=year, month=month, interval=ui_iv
        )
        if (
            not force_refresh
            and isinstance(cached, dict)
            and isinstance(cached.get("days"), list)
            and cached.get("days")
            and not cached.get("stale")
            and cached.get("strike") == cfg.strike
            and cached.get("underlying_symbol") == cfg.underlying_symbol
            and cached.get("ce_symbol") == cfg.ce_symbol
            and cached.get("pe_symbol") == cfg.pe_symbol
            and cached.get("interval", "1D") == ui_iv
        ):
            try:
                stored_metrics = await metrics_store.get_month_metrics(
                    tenant_id, year=year, month=month
                )
                return {
                    **cached,
                    "days": metrics_store.strip_embedded_metrics(
                        list(cached["days"])
                    ),
                    "metrics_by_day": metrics_store.normalize_metrics_by_day(
                        stored_metrics
                    ),
                }
            except Exception:  # noqa: BLE001
                logger.warning(
                    "param_chart_metrics_merge_failed", tenant_id=tenant_id
                )
            return {
                **cached,
                "days": metrics_store.strip_embedded_metrics(list(cached["days"])),
                "metrics_by_day": metrics_store.normalize_metrics_by_day(
                    cached.get("metrics_by_day")
                    if isinstance(cached.get("metrics_by_day"), dict)
                    else {}
                ),
            }

        # SSE path: never block on a year-long Kite hist pull — UI/Refresh builds.
        if not build_missing and not force_refresh:
            return {
                "ok": True,
                "building": True,
                "stale": True,
                "year": year,
                "month": month,
                "interval": ui_iv,
                "underlying_symbol": cfg.underlying_symbol,
                "underlying_label": cfg.underlying_label,
                "strike": int(cfg.strike or 0),
                "entry_ce_premium": cfg.entry_ce_premium,
                "entry_pe_premium": cfg.entry_pe_premium,
                "entry_total": cfg.entry_total(),
                "ce_symbol": cfg.ce_symbol,
                "pe_symbol": cfg.pe_symbol,
                "fut_symbol": cfg.fut_symbol,
                "days": [],
                "kite": {
                    "source": "kite",
                    "interval": ui_iv,
                    "errors": ["pack_building"],
                },
                "built_at": int(time.time()),
            }

        got_lock = await pc_cache.try_rebuild_lock(
            tenant_id, year=year, month=month, interval=ui_iv
        )
        if not got_lock:
            # Another worker is rebuilding — return stub; client retries.
            return {
                "ok": True,
                "building": True,
                "stale": True,
                "year": year,
                "month": month,
                "interval": ui_iv,
                "underlying_symbol": cfg.underlying_symbol,
                "underlying_label": cfg.underlying_label,
                "strike": int(cfg.strike or 0),
                "entry_ce_premium": cfg.entry_ce_premium,
                "entry_pe_premium": cfg.entry_pe_premium,
                "entry_total": cfg.entry_total(),
                "ce_symbol": cfg.ce_symbol,
                "pe_symbol": cfg.pe_symbol,
                "fut_symbol": cfg.fut_symbol,
                "days": list((cached or {}).get("days") or []),
                "kite": {
                    "source": "kite",
                    "interval": ui_iv,
                    "errors": ["pack_rebuild_in_progress"],
                },
                "built_at": int(time.time()),
            }

        try:
            # Re-check after lock — winner may have finished.
            cached2 = await pc_cache.get_month_pack(
                tenant_id, year=year, month=month, interval=ui_iv
            )
            if (
                not force_refresh
                and isinstance(cached2, dict)
                and isinstance(cached2.get("days"), list)
                and cached2.get("days")
                and not cached2.get("stale")
                and cached2.get("interval", "1D") == ui_iv
            ):
                return cached2

            strike = int(cfg.strike or 0)
            entry_total = cfg.entry_total()
            days: list[dict[str, Any]] = []
            if ui_iv == "1D":
                try:
                    from app.domains.options_lab_bots import load_nse_holidays_effective

                    holidays = await load_nse_holidays_effective()
                except Exception:  # noqa: BLE001
                    holidays = None
                days = [
                    _empty_day(day, day_index=i)
                    for i, day in enumerate(
                        _trading_days_in_month(year, month, holidays=holidays),
                        start=1,
                    )
                ]
            kite_meta: dict[str, Any] = {"source": "kite", "interval": ui_iv}
            try:
                days, kite_meta = await self._backfill_from_kite(
                    cfg, days, year=year, month=month, interval=ui_iv
                )
            except Exception:  # noqa: BLE001
                logger.warning("param_chart_kite_backfill_failed", tenant_id=tenant_id)
                kite_meta = {
                    "source": "kite",
                    "interval": ui_iv,
                    "errors": ["backfill_failed"],
                }

            try:
                stored_metrics = await metrics_store.get_month_metrics(
                    tenant_id, year=year, month=month
                )
            except Exception:  # noqa: BLE001
                stored_metrics = None
                logger.warning("param_chart_metrics_merge_failed", tenant_id=tenant_id)

            metrics_by_day = metrics_store.normalize_metrics_by_day(stored_metrics)
            days = metrics_store.strip_embedded_metrics(days)

            ohlc_hits = sum(1 for d in days if d.get("close") is not None)
            stale = ohlc_hits == 0 and bool(kite_meta.get("errors"))

            pack = {
                "ok": True,
                "stale": stale,
                "year": year,
                "month": month,
                "interval": ui_iv,
                "underlying_symbol": cfg.underlying_symbol,
                "underlying_label": cfg.underlying_label,
                "strike": strike,
                "entry_ce_premium": cfg.entry_ce_premium,
                "entry_pe_premium": cfg.entry_pe_premium,
                "entry_total": entry_total,
                "ce_symbol": cfg.ce_symbol,
                "pe_symbol": cfg.pe_symbol,
                "fut_symbol": cfg.fut_symbol,
                "days": days,
                "metrics_by_day": metrics_by_day,
                "kite": kite_meta,
                "built_at": int(time.time()),
            }
            try:
                from app.domains import param_chart_token_store as token_store

                if kite_meta.get("ce_token") and cfg.ce_symbol:
                    await token_store.put_instrument_token(
                        cfg.ce_symbol, int(kite_meta["ce_token"])
                    )
                if kite_meta.get("pe_token") and cfg.pe_symbol:
                    await token_store.put_instrument_token(
                        cfg.pe_symbol, int(kite_meta["pe_token"])
                    )
                if kite_meta.get("spot_token") and cfg.underlying_symbol:
                    await token_store.put_instrument_token(
                        cfg.underlying_symbol, int(kite_meta["spot_token"])
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "param_chart_token_persist_failed",
                    tenant_id=tenant_id,
                    error=str(exc)[:200],
                )
            await pc_cache.set_month_pack(
                tenant_id, year=year, month=month, interval=ui_iv, payload=pack
            )
            return pack
        finally:
            await pc_cache.release_rebuild_lock(
                tenant_id, year=year, month=month, interval=ui_iv
            )

    async def _live_quote_overlay(self, cfg: ParamChartConfig) -> dict[str, Any]:
        """Book-first spot/CE/PE + Signal shared metrics (no hist)."""
        return await live_quote_overlay(_tenant_key(self.context), cfg)

    async def _today_overlay(self, cfg: ParamChartConfig, pack: dict[str, Any]) -> dict[str, Any]:
        """Live today: ticker book for spot/CE/PE + Signal shared metrics overlay."""
        live = await live_quote_overlay(_tenant_key(self.context), cfg)
        return apply_today_overlay(cfg, pack, live)

    async def month_state(
        self,
        *,
        year: int | None = None,
        month: int | None = None,
        interval: str | None = None,
        force_refresh: bool = False,
        build_missing: bool = True,
        persist_metrics: bool = True,
    ) -> dict[str, Any]:
        cfg = await self._read_config()
        saved_cfg = cfg
        y, m = cfg.resolved_year_month()
        if year:
            y = year
        if month:
            m = month
        if interval:
            cfg = replace(cfg, interval=_normalize_interval(interval))
        cfg = await self._heal_option_symbols_for_month(cfg, year=y, month=m)
        pack = await self._ensure_month_skeleton(
            cfg,
            year=y,
            month=m,
            force_refresh=force_refresh,
            build_missing=build_missing,
        )
        admin_cfg = (
            saved_cfg.to_admin_dict() if interval else cfg.to_admin_dict()
        )
        # Soft/building stubs must not fabricate a lone "today" bar — SSE would
        # otherwise replace a full intraday hist with one empty row.
        if pack.get("building"):
            await pc_cache.touch_watcher(_tenant_key(self.context))
            live = await self._live_quote_overlay(cfg)
            live_metrics = (
                live["live_metrics"]
                if isinstance(live.get("live_metrics"), dict)
                else {}
            )
            today_s = _ist_today().isoformat()
            mbd = metrics_store.normalize_metrics_by_day(
                pack.get("metrics_by_day")
                if isinstance(pack.get("metrics_by_day"), dict)
                else {}
            )
            if live_metrics:
                mbd[today_s] = {
                    **(mbd.get(today_s) if isinstance(mbd.get(today_s), dict) else {}),
                    **live_metrics,
                }
            out = {
                **pack,
                "ok": True,
                "days": list(pack.get("days") or []),
                "metrics_by_day": mbd,
                "today": today_s,
                "live_metrics": live_metrics,
                "kite_live": live.get("kite_live") or {"source": "book", "error": None},
                "fetched_at": int(time.time()),
                "stream_interval_ms": STREAM_INTERVAL_MS,
                "config": admin_cfg,
                "shared_metrics": shared_metric_defs(),
                "shared_categories": shared_categories(),
            }
            if live.get("quote_stale"):
                out["quote_stale"] = True
                out["quote_reference"] = live.get("quote_reference") or "previous_close"
            return out

        merged = await self._today_overlay(cfg, pack)
        # Persist shared checklist metrics into today's day card (history for overlays).
        # Skip on the SSE/soft path — never stamp EOD from a live tick.
        if persist_metrics and not pack.get("building"):
            try:
                await self.persist_metrics_from_signal_snapshot(force=False)
            except Exception:  # noqa: BLE001
                logger.warning("param_chart_metrics_persist_failed")
        await pc_cache.touch_watcher(_tenant_key(self.context))
        return {
            **merged,
            "ok": True,
            "config": admin_cfg,
            "shared_metrics": shared_metric_defs(),
            "shared_categories": shared_categories(),
            "data_source": "kite",
            "poll_ms": STREAM_INTERVAL_MS,
        }

    async def persist_metrics_from_signal_snapshot(
        self,
        *,
        force: bool = False,
        snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Write shared-list metrics into today's month-pack day (EOD history).

        - During market hours: refresh at most every ~5 minutes (Redis gate).
        - After 15:30 IST: finalize once per day (stable overnight history).
        """
        cfg = await self._read_config()
        y, m = cfg.resolved_year_month()
        today = _ist_today()
        # Only stamp the currently configured month pack when it is "this month".
        if y != today.year or m != today.month:
            if not force:
                return None
        tenant_id = _tenant_key(self.context)
        now = datetime.now(IST)
        after_close = (now.hour, now.minute) >= (15, 30)
        day_s = today.isoformat()

        if not force:
            if after_close:
                if not await pc_cache.try_eod_finalize_gate(tenant_id, day=day_s):
                    return None
            else:
                if not await pc_cache.try_metrics_persist_gate(tenant_id, day=day_s):
                    return None

        snap = snapshot
        if snap is None:
            snap = await signal_cache.get_snapshot(tenant_id)
        if not isinstance(snap, dict):
            return None
        metrics = project_metrics_from_signal_rows(snap.get("metrics"))
        if not metrics and not force:
            return None

        pack = await self._ensure_month_skeleton(cfg, year=y, month=m)
        days = []
        found = False
        for row in pack.get("days") or []:
            if str(row.get("date") or "") == day_s or (
                str(row.get("date") or "").startswith(day_s)
            ):
                days.append({**row, "metrics": metrics, "metrics_at": int(time.time())})
                found = True
            else:
                days.append(row)
        if not found and today.weekday() < 5 and y == today.year and m == today.month:
            row = _empty_day(today, day_index=len(days) + 1)
            row["metrics"] = metrics
            row["metrics_at"] = int(time.time())
            days.append(row)

        pack = {
            **pack,
            "days": days,
            "metrics_persisted_at": int(time.time()),
        }
        if after_close:
            pack["eod_at"] = int(time.time())
        # Durable cold store survives Redis TTL / interval switches / rebuilds.
        try:
            await metrics_store.upsert_day_metrics(
                tenant_id,
                year=y,
                month=m,
                day=day_s,
                metrics=metrics,
            )
        except Exception:  # noqa: BLE001
            logger.warning("param_chart_metrics_cold_put_failed", tenant_id=tenant_id)
        await pc_cache.set_month_pack(
            tenant_id,
            year=y,
            month=m,
            interval=_normalize_interval(cfg.interval),
            payload=pack,
        )
        logger.info(
            "param_chart_metrics_persisted",
            tenant_id=tenant_id,
            day=day_s,
            n_metrics=len(metrics),
            eod=after_close,
        )
        return pack

    async def persist_eod_from_signal_snapshot(self) -> dict[str, Any] | None:
        """Backward-compatible alias — force EOD-style metrics persist."""
        return await self.persist_metrics_from_signal_snapshot(force=True)


def _slim_stream_frame(state: dict[str, Any]) -> dict[str, Any]:
    """SSE payload: today bars + live metrics only (client keeps hist pack)."""
    today_s = str(state.get("today") or _ist_today().isoformat())
    building = bool(state.get("building"))
    days = state.get("days") if isinstance(state.get("days"), list) else []
    # Never ship day replacements while the hist pack is still building — the
    # client would clobber a REST-loaded month with an empty/stub today list.
    today_days: list[dict[str, Any]] = []
    if not building:
        today_days = [
            {**d, "metrics": {}}
            for d in days
            if isinstance(d, dict) and str(d.get("date") or "").startswith(today_s)
        ]
    live = state.get("live_metrics") if isinstance(state.get("live_metrics"), dict) else {}
    mbd_in = state.get("metrics_by_day") if isinstance(state.get("metrics_by_day"), dict) else {}
    today_metrics = mbd_in.get(today_s) if isinstance(mbd_in.get(today_s), dict) else {}
    metrics_by_day = {today_s: {**today_metrics, **live}} if (today_metrics or live) else {}
    return {
        "ok": True,
        "stream_patch": True,
        "building": building,
        "stale": state.get("stale"),
        "year": state.get("year"),
        "month": state.get("month"),
        "interval": state.get("interval")
        or (state.get("config") or {}).get("interval"),
        "today": today_s,
        "days": today_days,
        "metrics_by_day": metrics_by_day,
        "live_metrics": live,
        "kite_live": state.get("kite_live"),
        "kite": state.get("kite"),
        "config": state.get("config"),
        "fetched_at": state.get("fetched_at"),
        "stream_interval_ms": state.get("stream_interval_ms") or STREAM_INTERVAL_MS,
        "quote_stale": state.get("quote_stale"),
        "quote_reference": state.get("quote_reference"),
        # shared_metrics / categories are static — client already has them from
        # REST /config; omit to keep 8 Hz frames small.
    }


async def overlay_frame_from_cache(tenant_id: str) -> dict[str, Any] | None:
    """SSE hot path: Redis overlay only (no Postgres / Kite)."""
    await pc_cache.touch_watcher(tenant_id)
    frame = await pc_cache.get_overlay(tenant_id)
    return frame if isinstance(frame, dict) else None


async def month_state_for_stream(
    session: AsyncSession,
    context: Any,
) -> dict[str, Any]:
    # Soft: never run Kite year rebuilds or EOD persist on the live SSE loop.
    full = await ParamChartService(session, context).month_state(
        build_missing=False,
        persist_metrics=False,
    )
    slim = _slim_stream_frame(full)
    # Overlay cache is the live path — do not persist a hist-building stub or
    # SSE will keep serving building=True after the month pack expires.
    slim["building"] = False
    await pc_cache.set_overlay(_tenant_key(context), slim)
    return slim


def param_chart_watch_symbols(cfg: ParamChartConfig) -> list[str]:
    """Under / FUT / CE / PE for the shared Kite hub (Param Chart source)."""
    return list(
        dict.fromkeys(
            s
            for s in (
                cfg.underlying_symbol,
                cfg.fut_symbol,
                cfg.ce_symbol,
                cfg.pe_symbol,
            )
            if s
        )
    )
