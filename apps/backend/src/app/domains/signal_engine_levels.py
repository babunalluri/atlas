"""Compute support/resistance levels from Kite historical candles."""

from __future__ import annotations

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
    }
