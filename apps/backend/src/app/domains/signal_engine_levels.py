"""Compute support/resistance levels from Kite historical candles."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any


def _candle_rows(candles: list[Any]) -> list[tuple[float, float, float, float]]:
    rows: list[tuple[float, float, float, float]] = []
    for row in candles:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            o, h, l, c = float(row[1]), float(row[2]), float(row[3]), float(row[4])
        except (TypeError, ValueError, IndexError):
            continue
        rows.append((o, h, l, c))
    return rows


def _classic_pivot(prev_high: float, prev_low: float, prev_close: float) -> float:
    return round((prev_high + prev_low + prev_close) / 3.0, 2)


def _cpr_bands(prev_high: float, prev_low: float, prev_close: float) -> tuple[float, float, float]:
    pivot = _classic_pivot(prev_high, prev_low, prev_close)
    bc = (prev_high + prev_low) / 2.0
    tc = (pivot - bc) + pivot
    top = max(pivot, bc, tc)
    bottom = min(pivot, bc, tc)
    return round(pivot, 2), round(top, 2), round(bottom, 2)


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return round(sum(values[-period:]) / period, 2)


def apply_spot_derived_fields(out: dict[str, float], spot: float | None) -> None:
    """Refresh live spot comparisons (safe to call after cached level merge)."""
    if spot is None or not out:
        return
    s = float(spot)
    if "pivot_point" in out:
        out["spot_vs_pivot"] = round(s - out["pivot_point"], 2)
    if "prev_day_high" in out and "prev_day_low" in out:
        inside = out["prev_day_low"] <= s <= out["prev_day_high"]
        out["inside_prev_day_range"] = 1.0 if inside else 0.0
    if "first_5m_high" in out and "first_5m_low" in out:
        inside5 = out["first_5m_low"] <= s <= out["first_5m_high"]
        out["inside_first_5m_range"] = 1.0 if inside5 else 0.0
    if "day_high" in out and "day_low" in out:
        inside_day = out["day_low"] <= s <= out["day_high"]
        out["inside_day_range"] = 1.0 if inside_day else 0.0
    if "sma20_5m" in out:
        out["spot_vs_sma20_5m"] = round(s - out["sma20_5m"], 2)


def levels_from_candles(
    *,
    daily_candles: list[Any],
    intraday_5m: list[Any],
    spot: float | None,
) -> dict[str, float]:
    """Derive Trade Desk level fields from OHLC candles."""
    out: dict[str, float] = {}
    daily = _candle_rows(daily_candles)
    intra = _candle_rows(intraday_5m)

    if len(daily) >= 2:
        _, ph, pl, pc = daily[-2]
        out["prev_day_high"] = ph
        out["prev_day_low"] = pl
        out["prev_day_close"] = pc
        pivot, cpr_top, cpr_bottom = _cpr_bands(ph, pl, pc)
        out["pivot_point"] = pivot
        out["cpr_top"] = cpr_top
        out["cpr_bottom"] = cpr_bottom

    if daily:
        _, dh, dl, _ = daily[-1]
        out["day_high"] = dh
        out["day_low"] = dl

    if intra:
        first = intra[0]
        out["first_5m_high"] = first[1]
        out["first_5m_low"] = first[2]
        closes = [c for *_, c in intra]
        sma = _sma(closes, 20)
        if sma is not None:
            out["sma20_5m"] = sma

    if spot is not None and out:
        apply_spot_derived_fields(out, spot)

    return out


def _parse_candle_date(raw: Any) -> date | None:
    if raw is None:
        return None
    text = str(raw).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _daily_ohlc_by_date(candles: list[Any]) -> dict[date, tuple[float, float, float, float]]:
    out: dict[date, tuple[float, float, float, float]] = {}
    for row in candles:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        day = _parse_candle_date(row[0])
        if day is None:
            continue
        try:
            out[day] = (float(row[1]), float(row[2]), float(row[3]), float(row[4]))
        except (TypeError, ValueError):
            continue
    return out


def _last_thursday_on_or_before(day: date) -> date:
    return day - timedelta(days=(day.weekday() - 3) % 7)


def _last_thursday_of_month(year: int, month: int) -> date:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    return _last_thursday_on_or_before(next_month - timedelta(days=1))


def _nearest_candle(
    by_date: dict[date, tuple[float, float, float, float]],
    target: date,
    *,
    max_shift: int = 7,
) -> tuple[float, float] | None:
    for offset in range(max_shift + 1):
        for candidate in (target - timedelta(days=offset), target + timedelta(days=offset)):
            row = by_date.get(candidate)
            if row is not None:
                return row[1], row[2]
    return None


def expiry_levels_from_daily(
    daily_candles: list[Any],
    *,
    ref: date | None = None,
) -> dict[str, float]:
    """Monthly / expiry session highs and lows for checklist #57–#60."""
    ref_day = ref or date.today()
    by_date = _daily_ohlc_by_date(daily_candles)
    if not by_date:
        return {}

    out: dict[str, float] = {}
    month_rows = [
        (day, ohlc)
        for day, ohlc in by_date.items()
        if day.year == ref_day.year and day.month == ref_day.month and day <= ref_day
    ]
    if month_rows:
        out["running_month_high"] = max(row[1] for _, row in month_rows)
        out["running_month_low"] = min(row[2] for _, row in month_rows)

    last_expiry = _last_thursday_on_or_before(ref_day - timedelta(days=1))
    last_expiry_hl = _nearest_candle(by_date, last_expiry)
    if last_expiry_hl is not None:
        out["last_expiry_high"], out["last_expiry_low"] = last_expiry_hl

    prev_month = ref_day.month - 1 if ref_day.month > 1 else 12
    prev_year = ref_day.year if ref_day.month > 1 else ref_day.year - 1
    prev_month_expiry = _last_thursday_of_month(prev_year, prev_month)
    prev_month_hl = _nearest_candle(by_date, prev_month_expiry)
    if prev_month_hl is not None:
        out["prev_month_expiry_high"], out["prev_month_expiry_low"] = prev_month_hl

    boundary_highs: list[float] = []
    boundary_lows: list[float] = []
    for boundary_day in (
        prev_month_expiry + timedelta(days=1),
        date(ref_day.year, ref_day.month, 1),
    ):
        hl = _nearest_candle(by_date, boundary_day, max_shift=3)
        if hl is not None:
            boundary_highs.append(hl[0])
            boundary_lows.append(hl[1])
    if boundary_highs and boundary_lows:
        out["expiry_boundary_high"] = max(boundary_highs)
        out["expiry_boundary_low"] = min(boundary_lows)

    return out


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> list[float]:
    trs: list[float] = []
    for idx in range(len(closes)):
        if idx == 0:
            tr = highs[idx] - lows[idx]
        else:
            tr = max(
                highs[idx] - lows[idx],
                abs(highs[idx] - closes[idx - 1]),
                abs(lows[idx] - closes[idx - 1]),
            )
        trs.append(tr)
    if len(trs) < period:
        return []
    out = [sum(trs[:period]) / period]
    for idx in range(period, len(trs)):
        out.append((out[-1] * (period - 1) + trs[idx]) / period)
    return out


def _supertrend_direction(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    period: int = 10,
    multiplier: float = 3.0,
) -> float | None:
    if len(closes) < period + 1:
        return None
    atr_vals = _atr(highs, lows, closes, period)
    if not atr_vals:
        return None
    final_upper = final_lower = trend = None
    for idx in range(period - 1, len(closes)):
        atr = atr_vals[idx - (period - 1)]
        hl2 = (highs[idx] + lows[idx]) / 2.0
        basic_upper = hl2 + multiplier * atr
        basic_lower = hl2 - multiplier * atr
        if final_upper is None:
            final_upper, final_lower, trend = basic_upper, basic_lower, 1.0
            continue
        if basic_upper < final_upper or closes[idx - 1] > final_upper:
            final_upper = basic_upper
        if basic_lower > final_lower or closes[idx - 1] < final_lower:
            final_lower = basic_lower
        if trend == 1.0 and closes[idx] < final_lower:
            trend = -1.0
        elif trend == -1.0 and closes[idx] > final_upper:
            trend = 1.0
    return trend


def _last_bar_change_pct(candles: list[Any]) -> float | None:
    """Percent change of the latest completed bar vs the prior bar."""
    rows = _candle_rows(candles)
    if len(rows) < 2:
        return None
    prev_close = rows[-2][3]
    last_close = rows[-1][3]
    if prev_close == 0:
        return None
    return round((last_close - prev_close) / prev_close * 100.0, 3)


def _daily_period_change_pct(candles: list[Any], lookback_bars: int) -> float | None:
    """Percent change over the last N daily closes (week ≈ 5, month ≈ 22 sessions)."""
    rows = _candle_rows(candles)
    if len(rows) < lookback_bars + 1:
        return None
    start_close = rows[-(lookback_bars + 1)][3]
    end_close = rows[-1][3]
    if start_close == 0:
        return None
    return round((end_close - start_close) / start_close * 100.0, 3)


def chart_timeframe_snapshots(
    *,
    minute_candles: list[Any] | None = None,
    five_min_candles: list[Any] | None = None,
    hour_candles: list[Any] | None = None,
    daily_candles: list[Any] | None = None,
) -> dict[str, float]:
    """Desk chart review (#51–#56): last-bar or period % change from Kite OHLC."""
    out: dict[str, float] = {}
    if minute_candles:
        chg = _last_bar_change_pct(minute_candles)
        if chg is not None:
            out["chart_1m_bar_chg_pct"] = chg
    if five_min_candles:
        chg = _last_bar_change_pct(five_min_candles)
        if chg is not None:
            out["chart_5m_bar_chg_pct"] = chg
    if hour_candles:
        chg = _last_bar_change_pct(hour_candles)
        if chg is not None:
            out["chart_60m_bar_chg_pct"] = chg
    if daily_candles:
        chg = _last_bar_change_pct(daily_candles)
        if chg is not None:
            out["chart_1d_bar_chg_pct"] = chg
        week = _daily_period_change_pct(daily_candles, 5)
        if week is not None:
            out["chart_1w_bar_chg_pct"] = week
        month = _daily_period_change_pct(daily_candles, 22)
        if month is not None:
            out["chart_1mo_bar_chg_pct"] = month
    return out


def contextual_desk_chart_feeds(feed: dict[str, Any]) -> dict[str, float]:
    """Conditional chart feeds for subjective desk rows (#20, #33) — display only."""
    out: dict[str, float] = {}
    points = feed.get("nifty_points_move")
    chg_1m = feed.get("chart_1m_bar_chg_pct")
    if points is not None and chg_1m is not None and abs(float(points)) >= 50.0:
        out["chart_1m_post_big_move_pct"] = float(chg_1m)

    hour = feed.get("ist_hour")
    chg_5m = feed.get("chart_5m_bar_chg_pct")
    if hour is not None and chg_5m is not None and 15.0 <= float(hour) < 15.5:
        out["chart_5m_3pm_window_pct"] = float(chg_5m)
    return out


def intraday_indicators_from_candles(
    intraday_1m: list[Any],
    spot: float | None,
) -> dict[str, float]:
    """VWAP + SuperTrend for checklist #113 (Kite 1-minute candles)."""
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    volumes: list[float] = []
    for row in intraday_1m:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        try:
            highs.append(float(row[2]))
            lows.append(float(row[3]))
            closes.append(float(row[4]))
            volumes.append(float(row[5]))
        except (TypeError, ValueError):
            continue
    if not closes:
        return {}

    out: dict[str, float] = {}
    vol_sum = sum(volumes)
    if vol_sum > 0:
        vwap = sum(((h + l + c) / 3.0) * v for h, l, c, v in zip(highs, lows, closes, volumes)) / vol_sum
        out["vwap_1m"] = round(vwap, 2)
        if spot is not None and vwap != 0:
            out["vwap_distance_pct"] = round((float(spot) - vwap) / vwap * 100, 3)

    direction = _supertrend_direction(highs, lows, closes)
    if direction is not None:
        out["supertrend_dir"] = direction

    return out


def mock_levels(spot: float = 24312.5) -> dict[str, float]:
    return {
        "prev_day_high": spot + 120,
        "prev_day_low": spot - 95,
        "prev_day_close": spot - 20,
        "pivot_point": round(spot - 5, 2),
        "cpr_top": round(spot + 15, 2),
        "cpr_bottom": round(spot - 25, 2),
        "day_high": spot + 45,
        "day_low": spot - 30,
        "first_5m_high": spot + 12,
        "first_5m_low": spot - 8,
        "sma20_5m": spot - 6,
        "inside_prev_day_range": 1.0,
        "inside_first_5m_range": 1.0,
        "inside_day_range": 1.0,
        "spot_vs_pivot": 5.0,
        "spot_vs_sma20_5m": 6.0,
        "running_month_high": spot + 180,
        "running_month_low": spot - 140,
        "last_expiry_high": spot + 90,
        "last_expiry_low": spot - 70,
        "prev_month_expiry_high": spot + 110,
        "prev_month_expiry_low": spot - 85,
        "expiry_boundary_high": spot + 60,
        "expiry_boundary_low": spot - 45,
        "vwap_1m": round(spot - 3, 2),
        "vwap_distance_pct": 0.12,
        "supertrend_dir": 1.0,
        "chart_1m_bar_chg_pct": 0.08,
        "chart_5m_bar_chg_pct": -0.12,
        "chart_60m_bar_chg_pct": 0.35,
        "chart_1d_bar_chg_pct": -0.45,
        "chart_1w_bar_chg_pct": 1.2,
        "chart_1mo_bar_chg_pct": 2.8,
        "chart_1m_post_big_move_pct": 0.05,
        "chart_5m_3pm_window_pct": -0.18,
    }
