"""Admin Options Lab — live option chain snapshots from Kite quotes."""

from __future__ import annotations

import asyncio
import calendar
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains import options_lab_cache as ol_cache
from app.domains.kite_ticker_hub import quote_source_for_tenant
from app.domains.signal_engine import (
    SignalEngineService,
    UNDERLYING_PRESETS,
    _cache_get,
    _cache_set,
    _find_quote_row,
    _pick_float,
    _round_strike,
    _tenant_key,
)
from app.domains.signal_engine_constants import STREAM_INTERVAL_MS
from app.domains.options_lab_iv import (
    IVP_MIN_SAMPLES,
    build_iv_chart_payload,
    compute_ivp,
    generate_mock_iv_history,
    get_iv_series,
    ivp_for_symbol,
    record_atm_iv,
    reset_iv_history as clear_iv_history,
)
from app.domains.options_lab_underlyings import (
    EQUITY_FNO_SEED,
    equity_root_from_underlying,
    extract_instruments_csv,
    extract_instruments_rows,
    merge_presets,
    parse_nfo_instrument_rows,
    parse_nfo_instruments_csv,
    suggest_equity_fut_symbol,
)
from app.domains.signal_engine_cache import get_session_value, set_session_value
from app.domains.signal_engine_chain import (
    build_chain_symbols,
    chain_metrics_from_quotes,
    strike_ladder,
)

DEFAULT_WINGS = 15
MIN_WINGS = 5
MAX_WINGS = 25
SCREENER_WINGS = 5
# Keep enough intra-session detail while avoiding huge write amplification.
STRADDLE_HISTORY_MAX = 480  # ~8m at 1 point/s per strike (fetched_at is whole seconds)
STRADDLE_HISTORY_RESPONSE_MAX = 600  # chart-facing payload cap
STRADDLE_SERIES_NEIGHBORS = 1  # ATM ±1 → 3 series (was ±2 → 5)
OI_BASELINE_FIELD = "options_lab:oi_baseline"
STRADDLE_HISTORY_FIELD = "options_lab:straddle_history"
FLOWS_HISTORY_FIELD = "options_lab:flows_history"
FLOWS_HISTORY_MOCK_FIELD = "options_lab:flows_history_mock"
FLOWS_HISTORY_MAX_DAYS = 30
SCREENER_BASELINE_FIELD = "options_lab:screener_baseline"
INSTRUMENTS_CACHE_FIELD = "options_lab:nfo_underlyings"
INSTRUMENTS_CACHE_TTL_SEC = 12 * 60 * 60
OPTIONS_LAB_SETTINGS_KEY = "options_lab"

MONTH_CODES = (
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
)

FUT_ROOT_BY_UNDERLYING: dict[str, tuple[str, str]] = {
    "NSE:NIFTY 50": ("NFO", "NIFTY"),
    "NSE:NIFTY": ("NFO", "NIFTY"),
    "NSE:BANKNIFTY": ("NFO", "BANKNIFTY"),
    "NSE:FINNIFTY": ("NFO", "FINNIFTY"),
    "NSE:NIFTYNXT50": ("NFO", "NIFTYNXT50"),
    "NSE:MIDCPNIFTY": ("NFO", "MIDCPNIFTY"),
    "BSE:SENSEX": ("BFO", "SENSEX"),
}

DEFAULT_OPTIONS_LAB_CONFIG: dict[str, Any] = {
    "underlying_symbol": "NSE:NIFTY 50",
    "underlying_label": "NIFTY 50",
    "fut_symbol": "",
    "strike_step": 50,
    "mock": False,
}


def _clamp_wings(wings: int) -> int:
    return max(MIN_WINGS, min(MAX_WINGS, int(wings)))


@dataclass
class OptionsLabConfig:
    underlying_symbol: str = "NSE:NIFTY 50"
    underlying_label: str = "NIFTY 50"
    fut_symbol: str = ""
    strike_step: int = 50
    mock: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> OptionsLabConfig:
        merged = {**DEFAULT_OPTIONS_LAB_CONFIG, **(raw or {})}
        strike = merged.get("strike_step")
        try:
            strike_step = int(strike) if strike not in (None, "") else 50
        except (TypeError, ValueError):
            strike_step = 50
        return cls(
            underlying_symbol=str(merged.get("underlying_symbol") or "").strip(),
            underlying_label=str(merged.get("underlying_label") or "").strip(),
            fut_symbol=str(merged.get("fut_symbol") or "").strip(),
            strike_step=max(1, strike_step),
            mock=bool(merged.get("mock")),
        )

    def to_admin_dict(self) -> dict[str, Any]:
        return {
            "underlying_symbol": self.underlying_symbol,
            "underlying_label": self.underlying_label,
            "fut_symbol": self.fut_symbol,
            "strike_step": self.strike_step,
            "mock": self.mock,
        }

    def cache_fingerprint(self) -> str:
        return (
            f"{self.underlying_symbol}|{self.fut_symbol}|{self.strike_step}|"
            f"{int(self.mock)}"
        )


def _parse_greek(row: dict[str, Any] | None, key: str) -> float | None:
    if not row:
        return None
    greeks = row.get("greeks")
    if isinstance(greeks, dict):
        val = greeks.get(key)
        if val is not None:
            try:
                return round(float(val), 4)
            except (TypeError, ValueError):
                pass
    return _pick_float(row, key)


def _leg_from_quote(row: dict[str, Any] | None, *, symbol: str) -> dict[str, Any]:
    token_raw = (row or {}).get("instrument_token")
    try:
        instrument_token = int(token_raw) if token_raw is not None else None
    except (TypeError, ValueError):
        instrument_token = None
    return {
        "symbol": symbol,
        "instrument_token": instrument_token,
        "ltp": _pick_float(row or {}, "last_price", "ltp", "last"),
        "oi": _pick_float(row or {}, "open_interest", "oi"),
        "volume": _pick_float(row or {}, "volume"),
        "iv": _pick_float(row or {}, "implied_volatility", "iv"),
        "delta": _parse_greek(row, "delta"),
    }


def _build_rows(
    *,
    strikes: list[int],
    ce_symbols: list[str],
    pe_symbols: list[str],
    quotes: dict[str, Any],
    atm: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for strike, ce_sym, pe_sym in zip(strikes, ce_symbols, pe_symbols, strict=False):
        ce_row = _find_quote_row(quotes, ce_sym)
        pe_row = _find_quote_row(quotes, pe_sym)
        rows.append(
            {
                "strike": strike,
                "is_atm": strike == atm,
                "ce": _leg_from_quote(ce_row, symbol=ce_sym),
                "pe": _leg_from_quote(pe_row, symbol=pe_sym),
            }
        )
    return rows


def _last_thursday(year: int, month: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() != 3:
        d -= timedelta(days=1)
    return d


def _active_fut_month(when: datetime) -> tuple[int, int]:
    ist = (
        when.astimezone(ZoneInfo("Asia/Kolkata"))
        if when.tzinfo
        else when.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
    )
    ref = ist.date()
    year, month = ref.year, ref.month
    if ref > _last_thursday(year, month):
        month += 1
        if month > 12:
            month = 1
            year += 1
    return year, month


def _fut_matches_underlying(fut_symbol: str, underlying_symbol: str) -> bool:
    fut = (fut_symbol or "").strip().upper()
    if not fut:
        return False
    tradingsymbol = fut.split(":", 1)[-1]
    meta = FUT_ROOT_BY_UNDERLYING.get(underlying_symbol.strip())
    root = meta[1] if meta else equity_root_from_underlying(underlying_symbol)
    if not root:
        return False
    return tradingsymbol.startswith(root.upper())


def suggest_fut_symbol(
    underlying_symbol: str,
    when: datetime | None = None,
) -> str:
    meta = FUT_ROOT_BY_UNDERLYING.get(underlying_symbol.strip())
    if meta:
        exchange, root = meta
        now = when or datetime.now(ZoneInfo("Asia/Kolkata"))
        year, month = _active_fut_month(now)
        yy = str(year)[-2:]
        mon = MONTH_CODES[month - 1]
        return f"{exchange}:{root}{yy}{mon}FUT"
    return suggest_equity_fut_symbol(underlying_symbol, when=when)


def screener_presets(
    universe: str = "indices",
    *,
    equity_presets: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    indices = []
    seen: set[str] = set()
    for preset in UNDERLYING_PRESETS:
        symbol = str(preset.get("symbol") or "").strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        row = dict(preset)
        row.setdefault("universe", "indices")
        indices.append(row)

    equities = list(equity_presets) if equity_presets is not None else list(EQUITY_FNO_SEED)
    for row in equities:
        row.setdefault("universe", "equities")

    if universe == "indices":
        return indices
    if universe == "equities":
        return merge_presets(equities)
    if universe == "all":
        return merge_presets(indices, equities)
    return indices


def _atm_metrics_from_rows(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    for row in rows:
        if not row.get("is_atm"):
            continue
        ce = row.get("ce") or {}
        pe = row.get("pe") or {}
        ce_iv = ce.get("iv")
        pe_iv = pe.get("iv")
        ce_ltp = ce.get("ltp")
        pe_ltp = pe.get("ltp")
        atm_iv: float | None = None
        if ce_iv is not None and pe_iv is not None:
            atm_iv = round((float(ce_iv) + float(pe_iv)) / 2, 2)
        elif ce_iv is not None:
            atm_iv = round(float(ce_iv), 2)
        elif pe_iv is not None:
            atm_iv = round(float(pe_iv), 2)
        straddle: float | None = None
        if ce_ltp is not None and pe_ltp is not None:
            straddle = round(float(ce_ltp) + float(pe_ltp), 2)
        return {"atm_iv": atm_iv, "straddle": straddle}
    return {"atm_iv": None, "straddle": None}


def _pct_change(current: float | None, baseline: float | None) -> float | None:
    if current is None or baseline is None or baseline == 0:
        return None
    return round((current - baseline) / abs(baseline) * 100, 2)


async def ensure_screener_baselines(
    tenant_id: str,
    entries: dict[str, dict[str, float | None]],
) -> dict[str, dict[str, float | None]]:
    today = _ist_trading_day()
    stored = await get_session_value(tenant_id, SCREENER_BASELINE_FIELD)
    baselines: dict[str, dict[str, float | None]] = {}
    if (
        isinstance(stored, dict)
        and stored.get("day") == today
        and isinstance(stored.get("entries"), dict)
    ):
        baselines = dict(stored["entries"])

    changed = False
    for symbol, metrics in entries.items():
        if symbol not in baselines:
            baselines[symbol] = metrics
            changed = True

    if changed or not isinstance(stored, dict) or stored.get("day") != today:
        await set_session_value(
            tenant_id,
            SCREENER_BASELINE_FIELD,
            {"day": today, "set_at": int(time.time()), "entries": baselines},
        )
    return baselines


async def load_screener_baselines(tenant_id: str) -> dict[str, dict[str, float | None]]:
    stored = await get_session_value(tenant_id, SCREENER_BASELINE_FIELD)
    if (
        not isinstance(stored, dict)
        or stored.get("day") != _ist_trading_day()
        or not isinstance(stored.get("entries"), dict)
    ):
        return {}
    return dict(stored["entries"])


def apply_screener_session_deltas(
    rows: list[dict[str, Any]],
    baselines: dict[str, dict[str, float | None]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("error"):
            out.append(row)
            continue
        symbol = str(row.get("underlying_symbol") or "")
        base = baselines.get(symbol) or {}
        chain_oi = (row.get("chain_ce_oi") or 0) + (row.get("chain_pe_oi") or 0)
        base_chain_oi = (base.get("chain_ce_oi") or 0) + (base.get("chain_pe_oi") or 0)
        if base.get("chain_ce_oi") is None:
            base_chain_oi = None
        out.append(
            {
                **row,
                "oi_pct_chg": _pct_change(chain_oi or None, base_chain_oi),
                "iv_chg": _pct_change(row.get("atm_iv"), base.get("atm_iv")),
            }
        )
    return out


async def apply_screener_ivp(
    tenant_id: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("error") or row.get("atm_iv") is None:
            out.append(row)
            continue
        symbol = str(row.get("underlying_symbol") or "")
        series = await get_iv_series(tenant_id, symbol)
        samples = [float(r["iv"]) for r in series if r.get("iv") is not None]
        out.append({**row, "ivp": compute_ivp(samples, row.get("atm_iv"))})
    return out


def compose_screener_row(
    preset: dict[str, Any],
    *,
    spot: float | None,
    atm: int | None,
    fut_symbol: str,
    summary: dict[str, Any],
    rows: list[dict[str, Any]],
    error: str | None = None,
) -> dict[str, Any]:
    symbol = str(preset.get("symbol") or "")
    label = str(preset.get("label") or symbol)
    atm_metrics = _atm_metrics_from_rows(rows)

    return {
        "underlying_symbol": symbol,
        "underlying_label": label,
        "fut_symbol": fut_symbol,
        "spot": round(spot, 2) if spot is not None else None,
        "atm": atm,
        "atm_iv": atm_metrics["atm_iv"],
        "straddle": atm_metrics["straddle"],
        "pcr": summary.get("pcr"),
        "max_pain": summary.get("max_pain"),
        "chain_ce_oi": summary.get("chain_ce_oi"),
        "chain_pe_oi": summary.get("chain_pe_oi"),
        "oi_pct_chg": None,
        "iv_chg": None,
        "ivp": None,
        "error": error,
    }


def mock_screener_rows(presets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    spots = [24312.5, 51200.0, 23450.0, 22100.0, 11800.0, 81200.0]
    out: list[dict[str, Any]] = []
    for idx, preset in enumerate(presets):
        step = int(preset.get("strike_step") or 50)
        spot = spots[idx % len(spots)] + idx * 17.5
        atm = _round_strike(spot, step)
        fut = suggest_fut_symbol(str(preset.get("symbol") or ""))
        pcr = round(0.92 + idx * 0.08, 3)
        atm_iv = round(11.0 + idx * 1.4, 2)
        symbol = str(preset.get("symbol") or "")
        mock_history = generate_mock_iv_history(symbol, atm_iv)
        ivp = compute_ivp([float(p["iv"]) for p in mock_history], atm_iv)
        out.append(
            {
                "underlying_symbol": preset.get("symbol"),
                "underlying_label": preset.get("label"),
                "fut_symbol": fut,
                "spot": round(spot, 2),
                "atm": atm,
                "atm_iv": atm_iv,
                "straddle": round(120 + idx * 18, 2),
                "pcr": pcr,
                "max_pain": float(atm + step),
                "chain_ce_oi": 4_200_000 - idx * 120_000,
                "chain_pe_oi": 3_900_000 + idx * 90_000,
                "oi_pct_chg": round(idx * 1.8 - 2.5, 2),
                "iv_chg": round(-1.5 + idx * 0.7, 2),
                "ivp": ivp,
                "error": None,
            }
        )
    return out


def _ist_trading_day() -> str:
    return datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")


def _oi_delta(current: float | None, baseline: float | None) -> float | None:
    if current is None or baseline is None:
        return None
    return round(current - baseline, 0)


def build_oi_chart_rows(
    rows: list[dict[str, Any]],
    baseline_strikes: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    baseline = baseline_strikes or {}
    out: list[dict[str, Any]] = []
    for row in rows:
        strike_key = str(row["strike"])
        base = baseline.get(strike_key) if isinstance(baseline.get(strike_key), dict) else {}
        ce_oi = row["ce"].get("oi")
        pe_oi = row["pe"].get("oi")
        out.append(
            {
                "strike": row["strike"],
                "is_atm": bool(row.get("is_atm")),
                "ce_oi": ce_oi,
                "pe_oi": pe_oi,
                "ce_oi_chg": _oi_delta(ce_oi, base.get("ce_oi")),
                "pe_oi_chg": _oi_delta(pe_oi, base.get("pe_oi")),
            }
        )
    return out


def _atm_straddle_from_rows(
    rows: list[dict[str, Any]],
) -> tuple[float | None, float | None, float | None]:
    for row in rows:
        if not row.get("is_atm"):
            continue
        ce = row["ce"].get("ltp")
        pe = row["pe"].get("ltp")
        if ce is None or pe is None:
            return ce, pe, None
        return float(ce), float(pe), round(float(ce) + float(pe), 2)
    return None, None, None


def _straddle_legs_from_row(
    row: dict[str, Any],
) -> tuple[float | None, float | None, float | None]:
    ce = row.get("ce", {}).get("ltp") if isinstance(row.get("ce"), dict) else None
    pe = row.get("pe", {}).get("ltp") if isinstance(row.get("pe"), dict) else None
    if ce is None or pe is None:
        return (
            float(ce) if ce is not None else None,
            float(pe) if pe is not None else None,
            None,
        )
    return float(ce), float(pe), round(float(ce) + float(pe), 2)


def _multi_straddle_targets(
    rows: list[dict[str, Any]],
    atm: int | None,
    *,
    neighbors: int = 2,
) -> list[int]:
    """ATM ± N strike steps present in the chain (for multi-straddle history)."""
    strikes = sorted(
        {int(row["strike"]) for row in rows if row.get("strike") is not None}
    )
    if not strikes:
        return []
    center = int(atm) if atm is not None else strikes[len(strikes) // 2]
    if center not in strikes:
        center = min(strikes, key=lambda s: abs(s - center))
    idx = strikes.index(center)
    lo = max(0, idx - neighbors)
    hi = min(len(strikes), idx + neighbors + 1)
    return strikes[lo:hi]


def _baseline_strikes_from_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | None]]:
    strikes: dict[str, dict[str, float | None]] = {}
    for row in rows:
        strikes[str(row["strike"])] = {
            "ce_oi": row["ce"].get("oi"),
            "pe_oi": row["pe"].get("oi"),
        }
    return strikes


async def ensure_oi_baseline(
    tenant_id: str,
    fingerprint: str,
    rows: list[dict[str, Any]],
    *,
    force: bool = False,
) -> dict[str, Any]:
    today = _ist_trading_day()
    stored = await get_session_value(tenant_id, OI_BASELINE_FIELD)
    if (
        not force
        and isinstance(stored, dict)
        and stored.get("fingerprint") == fingerprint
        and stored.get("day") == today
        and isinstance(stored.get("strikes"), dict)
    ):
        strikes = dict(stored["strikes"])
        merged = _baseline_strikes_from_rows(rows)
        added = False
        for strike_key, leg in merged.items():
            if strike_key not in strikes:
                strikes[strike_key] = leg
                added = True
        if added:
            stored = {**stored, "strikes": strikes}
            await set_session_value(tenant_id, OI_BASELINE_FIELD, stored)
        return stored

    payload = {
        "fingerprint": fingerprint,
        "day": today,
        "set_at": int(time.time()),
        "strikes": _baseline_strikes_from_rows(rows),
    }
    await set_session_value(tenant_id, OI_BASELINE_FIELD, payload)
    return payload


async def append_straddle_point(
    tenant_id: str,
    fingerprint: str,
    rows: list[dict[str, Any]],
    *,
    fetched_at: int,
    atm: int | None,
) -> dict[str, Any]:
    """Append ATM + nearby-strike straddle samples for the IST session."""
    today = _ist_trading_day()
    stored = await get_session_value(tenant_id, STRADDLE_HISTORY_FIELD)
    if (
        not isinstance(stored, dict)
        or stored.get("fingerprint") != fingerprint
        or stored.get("day") != today
    ):
        stored = {"fingerprint": fingerprint, "day": today, "points": [], "series": {}}

    series: dict[str, list[dict[str, Any]]] = dict(stored.get("series") or {})
    targets = _multi_straddle_targets(rows, atm, neighbors=STRADDLE_SERIES_NEIGHBORS)
    row_by_strike = {int(row["strike"]): row for row in rows if row.get("strike") is not None}

    for strike in targets:
        row = row_by_strike.get(strike)
        if row is None:
            continue
        ce, pe, combined = _straddle_legs_from_row(row)
        if combined is None or ce is None or pe is None:
            continue
        key = str(strike)
        pts: list[dict[str, Any]] = list(series.get(key) or [])
        if not pts or pts[-1].get("t") != fetched_at:
            # Compact samples — strike lives in series key; atm only on response meta.
            pts.append(
                {
                    "t": fetched_at,
                    "ce": round(ce, 2),
                    "pe": round(pe, 2),
                    "combined": combined,
                }
            )
        if len(pts) > STRADDLE_HISTORY_MAX:
            pts = pts[-STRADDLE_HISTORY_MAX:]
        series[key] = pts

    # ATM series stays the primary `points` array for older clients (derived, not duplicated in Redis).
    atm_key = str(int(atm)) if atm is not None else None
    points: list[dict[str, Any]] = list(
        series.get(atm_key) if atm_key and atm_key in series else []
    )
    if not points:
        ce, pe, combined = _atm_straddle_from_rows(rows)
        if combined is not None and ce is not None and pe is not None:
            points = [
                {
                    "t": fetched_at,
                    "ce": round(ce, 2),
                    "pe": round(pe, 2),
                    "combined": combined,
                }
            ]

    await set_session_value(
        tenant_id,
        STRADDLE_HISTORY_FIELD,
        {
            "fingerprint": fingerprint,
            "day": today,
            # Do not persist duplicate `points` — derive from series[atm] on read.
            "series": series,
            "atm": atm,
        },
    )
    return {"points": points, "series": series, "strikes": targets}


def _flows_series_labels(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    today = _ist_trading_day()
    out: list[dict[str, Any]] = []
    for row in points:
        day = str(row.get("day") or "")
        label = "Today" if day == today else day
        item: dict[str, Any] = {
            "label": label,
            "day": day or None,
            "fii_net": row.get("fii_net"),
            "dii_net": row.get("dii_net"),
        }
        if row.get("mock"):
            item["mock"] = True
            if label != "Today":
                item["label"] = f"{label} (mock)"
        out.append(item)
    return out


async def append_flows_day(
    tenant_id: str,
    *,
    fii_net: float | None,
    dii_net: float | None,
    mock_seed: bool = False,
) -> list[dict[str, Any]]:
    """Upsert today's FII/DII sample into a multi-day session series.

    Mock history uses a separate Redis key so it cannot leak into live mode.
    """
    today = _ist_trading_day()
    field = FLOWS_HISTORY_MOCK_FIELD if mock_seed else FLOWS_HISTORY_FIELD
    stored = await get_session_value(tenant_id, field)
    points: list[dict[str, Any]] = []
    if isinstance(stored, dict) and isinstance(stored.get("points"), list):
        points = [dict(p) for p in stored["points"] if isinstance(p, dict)]

    if mock_seed and len(points) < 5:
        # Deterministic short history for Mock mode (not NSE archive replay).
        base_fii = float(fii_net) if fii_net is not None else 850.0
        base_dii = float(dii_net) if dii_net is not None else -120.0
        seeded: list[dict[str, Any]] = []
        for i in range(6, 0, -1):
            day = (
                datetime.now(ZoneInfo("Asia/Kolkata")) - timedelta(days=i)
            ).strftime("%Y-%m-%d")
            seeded.append(
                {
                    "day": day,
                    "fii_net": round(base_fii * (0.7 + 0.08 * i), 1),
                    "dii_net": round(base_dii * (0.6 + 0.07 * i), 1),
                    "mock": True,
                }
            )
        known = {p["day"] for p in seeded}
        for p in points:
            d = str(p.get("day") or "")
            if d and d not in known and d != today:
                seeded.append({**p, "mock": True})
        points = seeded

    if fii_net is not None or dii_net is not None:
        updated = False
        for p in points:
            if str(p.get("day")) == today:
                if fii_net is not None:
                    p["fii_net"] = fii_net
                if dii_net is not None:
                    p["dii_net"] = dii_net
                if mock_seed:
                    p["mock"] = True
                updated = True
                break
        if not updated:
            row: dict[str, Any] = {"day": today, "fii_net": fii_net, "dii_net": dii_net}
            if mock_seed:
                row["mock"] = True
            points.append(row)

    points.sort(key=lambda p: str(p.get("day") or ""))
    if len(points) > FLOWS_HISTORY_MAX_DAYS:
        points = points[-FLOWS_HISTORY_MAX_DAYS:]

    await set_session_value(tenant_id, field, {"points": points})
    return _flows_series_labels(points)


async def flows_series_snapshot(tenant_id: str, *, mock: bool = False) -> list[dict[str, Any]]:
    """Read stored multi-day flows without mutating."""
    field = FLOWS_HISTORY_MOCK_FIELD if mock else FLOWS_HISTORY_FIELD
    stored = await get_session_value(tenant_id, field)
    points: list[dict[str, Any]] = []
    if isinstance(stored, dict) and isinstance(stored.get("points"), list):
        points = [dict(p) for p in stored["points"] if isinstance(p, dict)]
    points.sort(key=lambda p: str(p.get("day") or ""))
    return _flows_series_labels(points)


def _downsample_straddle_points(
    points: list[dict[str, Any]],
    *,
    limit: int = STRADDLE_HISTORY_RESPONSE_MAX,
) -> list[dict[str, Any]]:
    if len(points) <= limit:
        return points
    # Keep first + last and uniformly sample interior points.
    keep = max(3, limit)
    stride = (len(points) - 1) / (keep - 1)
    sampled: list[dict[str, Any]] = []
    for i in range(keep):
        idx = round(i * stride)
        sampled.append(points[min(idx, len(points) - 1)])
    return sampled


async def attach_charts(
    tenant_id: str,
    fingerprint: str,
    payload: dict[str, Any],
    *,
    force_baseline: bool = False,
) -> dict[str, Any]:
    rows = payload.get("rows") or []
    if not rows or not payload.get("ok"):
        return payload

    baseline = await ensure_oi_baseline(
        tenant_id,
        fingerprint,
        rows,
        force=force_baseline,
    )
    straddle_hist = await append_straddle_point(
        tenant_id,
        fingerprint,
        rows,
        fetched_at=int(payload.get("fetched_at") or time.time()),
        atm=payload.get("atm"),
    )
    series_out: dict[str, list[dict[str, Any]]] = {}
    for key, pts in (straddle_hist.get("series") or {}).items():
        series_out[key] = _downsample_straddle_points(pts)
    atm_metrics = _atm_metrics_from_rows(rows)
    symbol = str(payload.get("underlying_symbol") or "")
    iv_chart = await build_iv_chart_payload(
        tenant_id,
        symbol,
        atm_metrics["atm_iv"],
        mock=bool(payload.get("mock")),
    )
    summary = dict(payload.get("summary") or {})
    summary["atm_iv"] = iv_chart.get("atm_iv")
    summary["ivp"] = iv_chart.get("ivp")
    return {
        **payload,
        "summary": summary,
        "charts": {
            "oi": build_oi_chart_rows(rows, baseline.get("strikes")),
            "oi_baseline_at": baseline.get("set_at"),
            "straddle": {
                "points": _downsample_straddle_points(straddle_hist.get("points") or []),
                "atm": payload.get("atm"),
                "series": series_out,
                "strikes": straddle_hist.get("strikes") or [],
            },
            "iv": iv_chart,
        },
    }


def mock_chain_snapshot(config: OptionsLabConfig, *, wings: int) -> dict[str, Any]:
    spot = 24312.5
    atm = _round_strike(spot, config.strike_step)
    strikes = strike_ladder(atm, config.strike_step, wings)
    fut = config.fut_symbol or "NFO:NIFTY26AUGFUT"
    _, ce_syms, pe_syms = build_chain_symbols(fut, atm, config.strike_step, wings)
    rows: list[dict[str, Any]] = []
    ce_oi_total = pe_oi_total = 0.0
    for idx, strike in enumerate(strikes):
        dist = abs(strike - atm)
        ce_oi = max(50_000, 420_000 - dist * 8_000 + idx * 1_000)
        pe_oi = max(40_000, 380_000 - dist * 7_500 + idx * 900)
        ce_oi_total += ce_oi
        pe_oi_total += pe_oi
        ce_ltp = max(1.0, 140 - dist * 2.5 + math.sin(idx) * 3)
        pe_ltp = max(1.0, 55 - dist * 1.2 + math.cos(idx) * 2)
        rows.append(
            {
                "strike": strike,
                "is_atm": strike == atm,
                "ce": {
                    "symbol": ce_syms[idx] if idx < len(ce_syms) else "",
                    "ltp": round(ce_ltp, 2),
                    "oi": ce_oi,
                    "volume": round(ce_oi / 120),
                    "iv": round(11.5 + dist * 0.04, 2),
                    "delta": round(max(0.05, 0.5 - (strike - spot) / 800), 4),
                },
                "pe": {
                    "symbol": pe_syms[idx] if idx < len(pe_syms) else "",
                    "ltp": round(pe_ltp, 2),
                    "oi": pe_oi,
                    "volume": round(pe_oi / 130),
                    "iv": round(12.0 + dist * 0.045, 2),
                    "delta": round(min(-0.05, -0.5 - (strike - spot) / 800), 4),
                },
            }
        )
    pcr = round(pe_oi_total / ce_oi_total, 3) if ce_oi_total > 0 else None
    return {
        "ok": True,
        "mock": True,
        "spot": spot,
        "atm": atm,
        "underlying_symbol": config.underlying_symbol or "NSE:NIFTY 50",
        "underlying_label": config.underlying_label or "NIFTY 50",
        "fut_symbol": fut,
        "strike_step": config.strike_step,
        "wings": wings,
        "fetched_at": int(time.time()),
        "warnings": [],
        "summary": {
            "pcr": pcr,
            "max_pain": float(atm + config.strike_step),
            "chain_ce_oi": ce_oi_total,
            "chain_pe_oi": pe_oi_total,
            "writer_grip_score": 0.28,
        },
        "rows": rows,
    }


async def chain_state_for_stream(
    service: "OptionsLabService",
    *,
    wings: int = DEFAULT_WINGS,
) -> dict[str, Any]:
    """Coalesce concurrent Options Lab stream readers to one chain tick per key."""
    wings = _clamp_wings(wings)
    config = await service._read_config()
    tenant_id = _tenant_key(service.context)
    fingerprint = config.cache_fingerprint()

    snapshot = await ol_cache.get_snapshot(
        tenant_id, wings=wings, fingerprint=fingerprint
    )
    if snapshot is not None:
        return _decorate_stream_payload(snapshot, tenant_id=tenant_id)

    if await ol_cache.try_compute_lock(tenant_id, wings=wings, fingerprint=fingerprint):
        try:
            snapshot = await ol_cache.get_snapshot(
                tenant_id, wings=wings, fingerprint=fingerprint
            )
            if snapshot is not None:
                return _decorate_stream_payload(snapshot, tenant_id=tenant_id)
            payload = await service.chain_snapshot(wings=wings)
            await ol_cache.set_snapshot(
                tenant_id, payload, wings=wings, fingerprint=fingerprint
            )
            return _decorate_stream_payload(payload, tenant_id=tenant_id)
        finally:
            await ol_cache.release_compute_lock(
                tenant_id, wings=wings, fingerprint=fingerprint
            )

    for _ in range(8):
        await asyncio.sleep(STREAM_INTERVAL_MS / 1000 / 4)
        snapshot = await ol_cache.get_snapshot(
            tenant_id, wings=wings, fingerprint=fingerprint
        )
        if snapshot is not None:
            return _decorate_stream_payload(snapshot, tenant_id=tenant_id)

    payload = await service.chain_snapshot(wings=wings)
    await ol_cache.set_snapshot(
        tenant_id, payload, wings=wings, fingerprint=fingerprint
    )
    return _decorate_stream_payload(payload, tenant_id=tenant_id)


def _decorate_stream_payload(payload: dict[str, Any], *, tenant_id: str) -> dict[str, Any]:
    out = dict(payload)
    out["poll_ms"] = STREAM_INTERVAL_MS
    out["stream"] = True
    out["quote_source"] = quote_source_for_tenant(tenant_id)
    return out


class OptionsLabService:
    def __init__(self, session: AsyncSession, context: Any) -> None:
        self.session = session
        self.context = context
        self.engine = SignalEngineService(session, context)

    async def _read_config(self) -> OptionsLabConfig:
        tool = await self.engine._signal_engine_tool()
        if tool is None:
            return OptionsLabConfig()
        settings = self.engine._tool_settings(tool)
        nested = settings.get(OPTIONS_LAB_SETTINGS_KEY)
        if isinstance(nested, dict):
            return OptionsLabConfig.from_dict(nested)
        return OptionsLabConfig()

    async def get_admin_config(self) -> dict[str, Any]:
        tool = await self.engine._signal_engine_tool()
        _, has_broker, team_ready = await self.engine._load_setup()
        equity_presets, equity_meta = await self._equity_presets()
        presets = merge_presets(
            [{**p, "universe": "indices"} for p in UNDERLYING_PRESETS],
            equity_presets,
        )
        return {
            "ok": True,
            "config": (await self._read_config()).to_admin_dict(),
            "presets": presets,
            "equity_source": equity_meta.get("source"),
            "equity_count": len(equity_presets),
            "tool_bound": tool is not None,
            "has_broker": has_broker,
            "team_ready": team_ready,
        }

    async def _equity_presets(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        from app.domains.desk_snapshot import DESK_TEAM_SLUGS, invoke_tool
        from app.domains.options_lab_trading import OptionsLabTradingService
        from app.domains.signal_engine import SIGNAL_TEAM_SLUG

        tenant_id = _tenant_key(self.context)
        cached = await get_session_value(tenant_id, INSTRUMENTS_CACHE_FIELD)
        now = int(time.time())
        if (
            isinstance(cached, dict)
            and isinstance(cached.get("presets"), list)
            and cached.get("presets")
            and int(cached.get("fetched_at") or 0) + INSTRUMENTS_CACHE_TTL_SEC > now
        ):
            return list(cached["presets"]), {
                "source": cached.get("source") or "cache",
                "fetched_at": cached.get("fetched_at"),
            }

        presets: list[dict[str, Any]] = []
        source = "seed"
        try:
            trading = OptionsLabTradingService(self.session, self.context)
            fn, name, _ = await trading._find_tool(
                ("get_instruments", "list_instruments"),
                team_slugs=(SIGNAL_TEAM_SLUG, *DESK_TEAM_SLUGS),
            )
            if fn is not None:
                raw = await invoke_tool(fn, {"exchange": "NFO"})
                rows = extract_instruments_rows(raw)
                if rows:
                    presets = parse_nfo_instrument_rows(rows)
                else:
                    csv_text = extract_instruments_csv(raw)
                    if csv_text:
                        presets = parse_nfo_instruments_csv(csv_text)
                if presets:
                    source = name or "instruments"
        except Exception:  # noqa: BLE001
            presets = []

        if not presets:
            presets = []
            for row in EQUITY_FNO_SEED:
                item = dict(row)
                item.setdefault("fut_symbol", suggest_fut_symbol(str(item["symbol"])))
                presets.append(item)
            source = "seed"

        await set_session_value(
            tenant_id,
            INSTRUMENTS_CACHE_FIELD,
            {"fetched_at": now, "source": source, "presets": presets},
        )
        return presets, {"source": source, "fetched_at": now}

    async def update_admin_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        tool = await self.engine._signal_engine_tool()
        if tool is None:
            return {
                "ok": False,
                "error": "Signal engine tool not bound on Signals ops team.",
            }
        current = self.engine._tool_settings(tool)
        lab = OptionsLabConfig.from_dict(
            current.get(OPTIONS_LAB_SETTINGS_KEY)
            if isinstance(current.get(OPTIONS_LAB_SETTINGS_KEY), dict)
            else None,
        )
        merged_lab = lab.to_admin_dict()
        for key in ("underlying_symbol", "underlying_label", "fut_symbol", "strike_step", "mock"):
            if key not in patch:
                continue
            val = patch[key]
            if val is None or val == "":
                if key != "mock":
                    merged_lab[key] = DEFAULT_OPTIONS_LAB_CONFIG.get(key, "")
                else:
                    merged_lab[key] = False
            else:
                merged_lab[key] = val
        # When underlying changes, fill FUT + strike_step from presets / suggest.
        # Full-config patches may still carry a stale FUT from the previous name.
        if "underlying_symbol" in patch and patch.get("underlying_symbol"):
            symbol = str(merged_lab["underlying_symbol"])
            equity_presets, _ = await self._equity_presets()
            all_presets = merge_presets(
                [{**p, "universe": "indices"} for p in UNDERLYING_PRESETS],
                equity_presets,
            )
            hit = next((p for p in all_presets if p.get("symbol") == symbol), None)
            if hit:
                if not patch.get("underlying_label"):
                    merged_lab["underlying_label"] = hit.get("label") or symbol
                if "strike_step" not in patch and hit.get("strike_step"):
                    merged_lab["strike_step"] = int(hit["strike_step"])
            suggested = ""
            if hit and hit.get("fut_symbol"):
                suggested = str(hit["fut_symbol"])
            if not suggested:
                suggested = suggest_fut_symbol(symbol)
            incoming_fut = str(merged_lab.get("fut_symbol") or "")
            if not _fut_matches_underlying(incoming_fut, symbol):
                merged_lab["fut_symbol"] = suggested
        next_config = OptionsLabConfig.from_dict(merged_lab)
        await self.engine._write_tool_settings(
            tool,
            {**current, OPTIONS_LAB_SETTINGS_KEY: next_config.to_admin_dict()},
        )
        return {"ok": True, **await self.get_admin_config()}

    async def reset_oi_baseline(self) -> dict[str, Any]:
        tenant_id = _tenant_key(self.context)
        await set_session_value(tenant_id, OI_BASELINE_FIELD, None)
        return {"ok": True}

    async def flows_snapshot(self) -> dict[str, Any]:
        """FII/DII net (₹ Cr) for Options Lab Flows overlay — independent of Signal Start."""
        from app.domains.signal_engine_nse import fetch_nse_slow_fields, mock_nse_fields

        config = await self._read_config()
        tenant_id = _tenant_key(self.context)
        if config.mock:
            fields = mock_nse_fields()
            series = await append_flows_day(
                tenant_id,
                fii_net=fields.get("fii_net"),
                dii_net=fields.get("dii_net"),
                mock_seed=True,
            )
            return {
                "ok": True,
                "mock": True,
                "fetched_at": int(time.time()),
                "fii_net": fields.get("fii_net"),
                "dii_net": fields.get("dii_net"),
                "advance_decline_ratio": fields.get("advance_decline_ratio"),
                "series": series,
            }

        try:
            fields = await asyncio.to_thread(fetch_nse_slow_fields)
        except Exception as exc:  # noqa: BLE001
            stored = await flows_series_snapshot(tenant_id, mock=False)
            return {
                "ok": False,
                "error": f"NSE flows unavailable: {exc}",
                "fetched_at": int(time.time()),
                "fii_net": None,
                "dii_net": None,
                "series": stored,
            }

        fii = fields.get("fii_net")
        dii = fields.get("dii_net")
        series = await append_flows_day(tenant_id, fii_net=fii, dii_net=dii, mock_seed=False)
        return {
            "ok": True,
            "mock": False,
            "fetched_at": int(time.time()),
            "fii_net": fii,
            "dii_net": dii,
            "advance_decline_ratio": fields.get("advance_decline_ratio"),
            "series": series,
            "warnings": (
                []
                if fii is not None or dii is not None
                else ["NSE FII/DII feed empty — try again later or enable Mock."]
            ),
        }

    async def list_gtts(self) -> dict[str, Any]:
        from app.domains.options_lab_trading import OptionsLabTradingService

        config = await self._read_config()
        trading = OptionsLabTradingService(self.session, self.context)
        return await trading.list_gtts(mock=bool(config.mock))

    async def delete_gtt(self, trigger_id: str | int) -> dict[str, Any]:
        from app.domains.options_lab_trading import OptionsLabTradingService

        config = await self._read_config()
        trading = OptionsLabTradingService(self.session, self.context)
        return await trading.delete_gtt(trigger_id, mock=bool(config.mock))

    async def _finalize_payload(
        self,
        payload: dict[str, Any],
        config: OptionsLabConfig,
        *,
        force_baseline: bool = False,
    ) -> dict[str, Any]:
        if not payload.get("ok"):
            return payload
        tenant_id = _tenant_key(self.context)
        return await attach_charts(
            tenant_id,
            config.cache_fingerprint(),
            payload,
            force_baseline=force_baseline,
        )

    async def chain_snapshot(self, *, wings: int = DEFAULT_WINGS) -> dict[str, Any]:
        wings = _clamp_wings(wings)
        config = await self._read_config()
        tenant_id = _tenant_key(self.context)
        cache_key = f"options_lab:chain:{wings}:{config.cache_fingerprint()}"
        cached = await _cache_get(tenant_id, cache_key)
        if isinstance(cached, dict) and cached.get("ok"):
            return await self._finalize_payload(cached, config)

        _, has_broker, team_ready = await self.engine._load_setup()
        warnings: list[str] = []
        if not team_ready:
            warnings.append("Publish the Signals ops team and bind Kite toolkit.")
        if not config.fut_symbol:
            warnings.append("Set FUT symbol in Options Lab setup (e.g. NFO:NIFTY26AUGFUT).")
        if not config.underlying_symbol:
            warnings.append("Select an underlying in Options Lab setup.")

        if config.mock:
            payload = mock_chain_snapshot(config, wings=wings)
            payload["warnings"] = warnings
            await _cache_set(tenant_id, cache_key, "broker", payload)
            return await self._finalize_payload(payload, config)

        if not has_broker:
            return {
                "ok": False,
                "error": "Kite (or broker) quotes not bound on Signals ops team.",
                "warnings": warnings,
                "wings": wings,
                "underlying_symbol": config.underlying_symbol,
                "fut_symbol": config.fut_symbol,
                "strike_step": config.strike_step,
            }

        if not config.fut_symbol or not config.underlying_symbol:
            return {
                "ok": False,
                "error": "Configure underlying and FUT symbol in Options Lab setup first.",
                "warnings": warnings,
                "wings": wings,
            }

        # Derive strikes from a cached spot quote when possible (single broker round-trip).
        spot_quotes = await self.engine._fetch_quote(
            [config.underlying_symbol],
            prefer="get_quote",
        )
        spot_row = _find_quote_row(spot_quotes, config.underlying_symbol)
        spot = _pick_float(spot_row or {}, "last_price", "ltp", "last")
        if spot is None:
            return {
                "ok": False,
                "error": f"No live quote for {config.underlying_symbol}.",
                "warnings": warnings,
                "wings": wings,
                "underlying_symbol": config.underlying_symbol,
                "fut_symbol": config.fut_symbol,
            }

        atm = _round_strike(spot, config.strike_step)
        strikes, ce_syms, pe_syms = build_chain_symbols(
            config.fut_symbol,
            atm,
            config.strike_step,
            wings,
        )
        if not ce_syms:
            return {
                "ok": False,
                "error": "Could not derive option symbols from FUT symbol.",
                "warnings": warnings,
            }

        symbols = list(dict.fromkeys(ce_syms + pe_syms))
        quotes = await self.engine._fetch_quote(symbols, prefer="get_quote")
        summary = chain_metrics_from_quotes(
            quotes,
            find_row=_find_quote_row,
            strikes=strikes,
            ce_symbols=ce_syms,
            pe_symbols=pe_syms,
        )
        rows = _build_rows(
            strikes=strikes,
            ce_symbols=ce_syms,
            pe_symbols=pe_syms,
            quotes=quotes,
            atm=atm,
        )

        payload = {
            "ok": True,
            "mock": False,
            "spot": round(spot, 2),
            "atm": atm,
            "underlying_symbol": config.underlying_symbol,
            "underlying_label": config.underlying_label or config.underlying_symbol,
            "fut_symbol": config.fut_symbol,
            "strike_step": config.strike_step,
            "wings": wings,
            "fetched_at": int(time.time()),
            "warnings": warnings,
            "summary": {
                "pcr": summary.get("pcr"),
                "max_pain": summary.get("max_pain"),
                "chain_ce_oi": summary.get("chain_ce_oi"),
                "chain_pe_oi": summary.get("chain_pe_oi"),
                "writer_grip_score": summary.get("writer_grip_score"),
            },
            "rows": rows,
        }
        await _cache_set(tenant_id, cache_key, "broker", payload)
        return await self._finalize_payload(payload, config)

    async def screener_snapshot(self, *, universe: str = "indices") -> dict[str, Any]:
        universe = universe if universe in {"indices", "equities", "all"} else "indices"
        config = await self._read_config()
        tenant_id = _tenant_key(self.context)
        cache_key = f"options_lab:screener:{universe}:{int(config.mock)}"
        cached = await _cache_get(tenant_id, cache_key)
        if isinstance(cached, dict) and cached.get("ok"):
            if cached.get("mock"):
                return cached
            baselines = await load_screener_baselines(tenant_id)
            rows = cached.get("rows") or []
            rows_with_deltas = apply_screener_session_deltas(list(rows), baselines)
            rows_with_deltas = await apply_screener_ivp(tenant_id, rows_with_deltas)
            return {
                **cached,
                "rows": rows_with_deltas,
            }

        equity_presets, equity_meta = await self._equity_presets()
        presets = screener_presets(universe, equity_presets=equity_presets)
        # Equities screener can be large — cap live scan for broker quote budget.
        if universe in {"equities", "all"} and len(presets) > 40:
            presets = presets[:40]
        _, has_broker, team_ready = await self.engine._load_setup()
        warnings: list[str] = []
        if not team_ready:
            warnings.append("Publish the Signals ops team and bind Kite toolkit.")
        if equity_meta.get("source") == "seed" and universe in {"equities", "all"}:
            warnings.append(
                "Equity list from seed — republish kite_toolkit with get_instruments "
                "for full NFO underlyings."
            )

        if config.mock:
            payload = {
                "ok": True,
                "mock": True,
                "universe": universe,
                "fetched_at": int(time.time()),
                "warnings": warnings,
                "rows": mock_screener_rows(presets),
            }
            tenant_id = _tenant_key(self.context)
            for row in payload["rows"]:
                if row.get("error") or row.get("atm_iv") is None:
                    continue
                symbol = str(row.get("underlying_symbol") or "")
                row["ivp"] = await ivp_for_symbol(
                    tenant_id,
                    symbol,
                    row.get("atm_iv"),
                    mock=True,
                )
            await _cache_set(tenant_id, cache_key, "medium", payload)
            return payload

        if not has_broker:
            return {
                "ok": False,
                "error": "Kite (or broker) quotes not bound on Signals ops team.",
                "universe": universe,
                "warnings": warnings,
            }

        scan_plans: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        spot_symbols: list[str] = []

        for preset in presets:
            symbol = str(preset.get("symbol") or "").strip()
            fut = str(preset.get("fut_symbol") or "").strip() or suggest_fut_symbol(symbol)
            if not fut:
                errors.append(
                    compose_screener_row(
                        preset,
                        spot=None,
                        atm=None,
                        fut_symbol="",
                        summary={},
                        rows=[],
                        error="FUT symbol mapping unavailable",
                    )
                )
                continue
            scan_plans.append(
                {
                    "preset": preset,
                    "symbol": symbol,
                    "fut": fut,
                    "strike_step": max(1, int(preset.get("strike_step") or 50)),
                }
            )
            spot_symbols.append(symbol)

        if not scan_plans and errors:
            return {
                "ok": True,
                "mock": False,
                "universe": universe,
                "fetched_at": int(time.time()),
                "warnings": warnings,
                "rows": errors,
            }

        spot_quotes = await self.engine._fetch_quote(
            list(dict.fromkeys(spot_symbols)),
            prefer="get_quote",
        )

        prepared: list[dict[str, Any]] = []
        option_symbols: list[str] = []

        for plan in scan_plans:
            preset = plan["preset"]
            symbol = plan["symbol"]
            fut = plan["fut"]
            strike_step = plan["strike_step"]
            spot_row = _find_quote_row(spot_quotes, symbol)
            spot = _pick_float(spot_row or {}, "last_price", "ltp", "last")
            if spot is None:
                errors.append(
                    compose_screener_row(
                        preset,
                        spot=None,
                        atm=None,
                        fut_symbol=fut,
                        summary={},
                        rows=[],
                        error=f"No live quote for {symbol}",
                    )
                )
                continue
            atm = _round_strike(spot, strike_step)
            strikes, ce_syms, pe_syms = build_chain_symbols(
                fut,
                atm,
                strike_step,
                SCREENER_WINGS,
            )
            if not ce_syms:
                errors.append(
                    compose_screener_row(
                        preset,
                        spot=spot,
                        atm=atm,
                        fut_symbol=fut,
                        summary={},
                        rows=[],
                        error="Could not derive option symbols",
                    )
                )
                continue
            prepared.append(
                {
                    "preset": preset,
                    "symbol": symbol,
                    "fut": fut,
                    "spot": spot,
                    "atm": atm,
                    "strikes": strikes,
                    "ce_syms": ce_syms,
                    "pe_syms": pe_syms,
                }
            )
            option_symbols.extend(ce_syms)
            option_symbols.extend(pe_syms)

        all_symbols = list(dict.fromkeys(option_symbols))
        quotes = dict(spot_quotes)
        if all_symbols:
            more = await self.engine._fetch_quote(all_symbols, prefer="get_quote")
            quotes.update(more)

        baseline_entries: dict[str, dict[str, float | None]] = {}
        rows_out: list[dict[str, Any]] = list(errors)

        for item in prepared:
            preset = item["preset"]
            symbol = item["symbol"]
            fut = item["fut"]
            summary = chain_metrics_from_quotes(
                quotes,
                find_row=_find_quote_row,
                strikes=item["strikes"],
                ce_symbols=item["ce_syms"],
                pe_symbols=item["pe_syms"],
            )
            chain_rows = _build_rows(
                strikes=item["strikes"],
                ce_symbols=item["ce_syms"],
                pe_symbols=item["pe_syms"],
                quotes=quotes,
                atm=item["atm"],
            )
            atm_metrics = _atm_metrics_from_rows(chain_rows)
            baseline_entries[symbol] = {
                "atm_iv": atm_metrics["atm_iv"],
                "chain_ce_oi": summary.get("chain_ce_oi"),
                "chain_pe_oi": summary.get("chain_pe_oi"),
            }
            rows_out.append(
                compose_screener_row(
                    preset,
                    spot=item["spot"],
                    atm=item["atm"],
                    fut_symbol=fut,
                    summary=summary,
                    rows=chain_rows,
                )
            )

        baselines = await ensure_screener_baselines(tenant_id, baseline_entries)
        rows_with_deltas = apply_screener_session_deltas(rows_out, baselines)
        rows_with_deltas = await apply_screener_ivp(tenant_id, rows_with_deltas)

        payload = {
            "ok": True,
            "mock": False,
            "universe": universe,
            "fetched_at": int(time.time()),
            "warnings": warnings,
            "rows": rows_with_deltas,
        }
        await _cache_set(tenant_id, cache_key, "medium", payload)
        return payload

    async def iv_history(self, *, symbol: str) -> dict[str, Any]:
        symbol = symbol.strip()
        if not symbol:
            return {"ok": False, "error": "symbol is required"}
        config = await self._read_config()
        tenant_id = _tenant_key(self.context)
        series = await get_iv_series(tenant_id, symbol)
        current_iv = series[-1]["iv"] if series else None
        if config.mock and len(series) < IVP_MIN_SAMPLES:
            await record_atm_iv(tenant_id, symbol, current_iv or 12.0, mock=True)
            series = await get_iv_series(tenant_id, symbol)
            if len(series) < IVP_MIN_SAMPLES:
                series = generate_mock_iv_history(symbol, current_iv or 12.0)
            current_iv = series[-1]["iv"] if series else None
        return {
            "ok": True,
            "symbol": symbol,
            "mock": config.mock,
            "points": series,
            "atm_iv": current_iv,
            "ivp": compute_ivp(
                [float(row["iv"]) for row in series if row.get("iv") is not None],
                float(current_iv) if current_iv is not None else None,
            ),
            "sample_days": len(series),
        }

    async def reset_iv_history(self) -> dict[str, Any]:
        tenant_id = _tenant_key(self.context)
        await clear_iv_history(tenant_id)
        return {"ok": True}

    async def reset_screener_baseline(self) -> dict[str, Any]:
        tenant_id = _tenant_key(self.context)
        await set_session_value(tenant_id, SCREENER_BASELINE_FIELD, None)
        return {"ok": True}

    async def list_portfolios(self) -> dict[str, Any]:
        from app.domains.options_lab_portfolios import list_portfolios

        tenant_id = _tenant_key(self.context)
        return await list_portfolios(tenant_id)

    async def create_portfolio(self, payload: dict[str, Any]) -> dict[str, Any]:
        from app.domains.options_lab_portfolios import create_portfolio

        tenant_id = _tenant_key(self.context)
        return await create_portfolio(tenant_id, payload)

    async def update_portfolio(self, portfolio_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        from app.domains.options_lab_portfolios import update_portfolio

        tenant_id = _tenant_key(self.context)
        return await update_portfolio(tenant_id, portfolio_id, patch)

    async def delete_portfolio(self, portfolio_id: str) -> dict[str, Any]:
        from app.domains.options_lab_portfolios import delete_portfolio

        tenant_id = _tenant_key(self.context)
        return await delete_portfolio(tenant_id, portfolio_id)

    async def mark_portfolio(self, portfolio_id: str) -> dict[str, Any]:
        from app.domains.options_lab_portfolios import (
            canonical_broker_option_symbol,
            get_portfolio,
            mark_portfolio_legs,
            _option_symbols_for_legs,
        )

        tenant_id = _tenant_key(self.context)
        config = await self._read_config()
        _, has_broker, _team_ready = await self.engine._load_setup()
        portfolio = await get_portfolio(tenant_id, portfolio_id)
        if portfolio is None:
            return {"ok": False, "error": "Portfolio not found."}

        fut = str(portfolio.get("fut_symbol") or config.fut_symbol or "").strip()
        legs = list(portfolio.get("legs") or [])
        leg_symbols = [
            canonical_broker_option_symbol(str(leg.get("symbol") or ""))
            for leg in legs
            if leg.get("symbol")
        ]
        symbols = _option_symbols_for_legs(fut, legs)
        for sym in leg_symbols:
            if sym and sym not in symbols:
                symbols.append(sym)

        if not fut and not config.mock and not symbols:
            return {"ok": False, "error": "Set FUT symbol on portfolio or in Options Lab setup."}
        if not config.mock and not has_broker and symbols:
            return {"ok": False, "error": "Kite (or broker) quotes not bound on Signals ops team."}

        quotes: dict[str, Any] = {}
        if config.mock:
            mock = mock_chain_snapshot(config, wings=SCREENER_WINGS)
            for row in mock.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                for key in ("ce", "pe"):
                    leg_data = row.get(key) or {}
                    sym = str(leg_data.get("symbol") or "").strip()
                    if sym:
                        quotes[sym] = {
                            "last_price": leg_data.get("ltp"),
                            "ltp": leg_data.get("ltp"),
                        }
        elif symbols:
            quotes = await self.engine._fetch_quote(symbols, prefer="get_quote")

        marked = mark_portfolio_legs(portfolio, quotes=quotes, mock=config.mock)
        return {"ok": True, **marked}

    async def import_kite_portfolio(self, *, name: str | None = None) -> dict[str, Any]:
        from app.domains.options_lab_portfolios import (
            create_portfolio,
            infer_fut_symbol_from_legs,
            kite_positions_payload,
        )

        config = await self._read_config()
        _, has_broker, team_ready = await self.engine._load_setup()
        warnings: list[str] = []
        if not team_ready:
            warnings.append("Publish the Signals ops team and bind Kite toolkit.")
        if not has_broker:
            return {
                "ok": False,
                "error": "Kite (or broker) not bound on Signals ops team.",
                "warnings": warnings,
            }

        raw = await self.engine._invoke_broker_tool("get_positions", {})
        legs, import_warnings = kite_positions_payload(raw)
        warnings.extend(import_warnings)
        if not legs:
            return {
                "ok": False,
                "error": "No F&O option positions to import.",
                "warnings": warnings,
            }

        portfolio_name = (name or "").strip() or f"Kite import {_now_label()}"
        fut_symbol = config.fut_symbol or infer_fut_symbol_from_legs(legs)
        payload = {
            "name": portfolio_name,
            "underlying_symbol": config.underlying_symbol,
            "underlying_label": config.underlying_label,
            "fut_symbol": fut_symbol,
            "strike_step": config.strike_step,
            "source": "kite_import",
            "legs": legs,
        }
        created = await self.create_portfolio(payload)
        if not created.get("ok"):
            return {**created, "warnings": warnings}
        marked = await self.mark_portfolio(str(created["portfolio"]["id"]))
        return {
            "ok": True,
            "portfolio": created["portfolio"],
            "mark": marked if marked.get("ok") else None,
            "warnings": warnings,
        }

    async def strategy_margins(self, payload: dict[str, Any]) -> dict[str, Any]:
        from app.domains.options_lab_trading import (
            OptionsLabTradingService,
            estimate_lot_size,
        )

        config = await self._read_config()
        legs = list(payload.get("legs") or [])
        underlying = str(
            payload.get("underlying_symbol")
            or config.underlying_symbol
            or config.underlying_label
            or ""
        )
        try:
            lot_size = int(payload.get("lot_size") or estimate_lot_size(underlying))
        except (TypeError, ValueError):
            lot_size = estimate_lot_size(underlying)
        product = str(payload.get("product") or "NRML")
        heuristic = payload.get("heuristic") if isinstance(payload.get("heuristic"), dict) else None
        trading = OptionsLabTradingService(self.session, self.context)
        return await trading.strategy_margins(
            legs=legs,
            lot_size=lot_size,
            product=product,
            mock=bool(config.mock or payload.get("mock")),
            heuristic=heuristic,
            basket=bool(payload.get("basket")),
        )

    async def place_strategy_orders(self, payload: dict[str, Any]) -> dict[str, Any]:
        from app.domains.options_lab_trading import (
            OptionsLabTradingService,
            estimate_lot_size,
        )

        config = await self._read_config()
        legs = list(payload.get("legs") or [])
        underlying = str(
            payload.get("underlying_symbol")
            or config.underlying_symbol
            or config.underlying_label
            or ""
        )
        try:
            lot_size = int(payload.get("lot_size") or estimate_lot_size(underlying))
        except (TypeError, ValueError):
            lot_size = estimate_lot_size(underlying)
        trading = OptionsLabTradingService(self.session, self.context)

        def _pct(key: str) -> float | None:
            raw = payload.get(key)
            if raw is None or raw == "":
                return None
            try:
                val = float(raw)
            except (TypeError, ValueError):
                return None
            return val if val > 0 else None

        placed = await trading.place_strategy_orders(
            legs=legs,
            lot_size=lot_size,
            product=str(payload.get("product") or "NRML"),
            order_type=str(payload.get("order_type") or "LIMIT"),
            confirm=bool(payload.get("confirm")),
            live=bool(payload.get("live")),
            mock=bool(config.mock or payload.get("mock")),
            tag=str(payload.get("tag") or "atlas-ol"),
            stop_loss_pct=_pct("stop_loss_pct"),
            target_pct=_pct("target_pct"),
            basket=bool(payload.get("basket")),
        )
        # Persist a draft only when at least one leg was submitted (or mock).
        draft = None
        if (
            bool(payload.get("confirm"))
            and payload.get("save_draft", True)
            and legs
            and (placed.get("mock") or int(placed.get("submitted_count") or 0) > 0)
        ):
            draft_legs = legs
            draft_name = str(payload.get("name") or f"Order {_now_label()}")
            if placed.get("partial"):
                submitted_idx = {
                    r.get("leg_index")
                    for r in (placed.get("orders") or [])
                    if r.get("status") == "submitted" and r.get("leg_index") is not None
                }
                draft_legs = [
                    leg for idx, leg in enumerate(legs) if idx in submitted_idx
                ]
                draft_name = f"PARTIAL {draft_name}"
            # Tag paper fills so Books → Paper filter finds them (name convention).
            if draft_legs:
                draft_name = paper_book_draft_name(draft_name, tool=placed.get("tool"))
                draft = await self.create_portfolio(
                    {
                        "name": draft_name,
                        "legs": draft_legs,
                        "underlying_symbol": config.underlying_symbol,
                        "underlying_label": config.underlying_label,
                        "fut_symbol": config.fut_symbol,
                    }
                )
        return {**placed, "draft": draft}


def _now_label() -> str:
    return datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d %b %H:%M")


def paper_book_draft_name(name: str, *, tool: str | None) -> str:
    """Prefix Paper books so the Books → Paper filter finds place_paper_order saves."""
    text = str(name or "").strip() or f"Order {_now_label()}"
    if tool == "place_paper_order" and not text.lower().startswith("paper"):
        return f"Paper {text}"
    return text
