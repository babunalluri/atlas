"""Multi-strike option OI → chain PCR and max pain (Kite quotes)."""

from __future__ import annotations

from typing import Any


def _derive_option_symbols(fut_symbol: str, strikes: list[int], side: str) -> list[str]:
    side = side.upper()
    raw = fut_symbol.strip()
    if not raw or side not in {"CE", "PE"}:
        return []
    if ":" in raw:
        exchange, sym = raw.split(":", 1)
    else:
        exchange, sym = "NFO", raw
    sym = sym.strip().upper()
    if not sym.endswith("FUT"):
        return []
    prefix = sym[:-3]
    return [f"{exchange.strip().upper()}:{prefix}{int(strike)}{side}" for strike in strikes]


def strike_ladder(atm: int, step: int, wings: int = 5) -> list[int]:
    step = max(step, 1)
    return [atm + step * i for i in range(-wings, wings + 1)]


def _max_pain_strike(strikes: list[int], ce_oi: dict[int, float], pe_oi: dict[int, float]) -> int | None:
    """Strike that minimizes writer pain across the ladder.

    Returns ``None`` when there is no OI (avoids returning ``min(strikes)`` —
    which is what a zero-OI ladder always produced) or when multiple strikes
    share the same minimum pain (ambiguous / flat distribution).
    """
    if not strikes:
        return None
    total_oi = sum(ce_oi.get(s, 0.0) for s in strikes) + sum(
        pe_oi.get(s, 0.0) for s in strikes
    )
    if total_oi <= 0:
        return None

    best_strike: int | None = None
    best_pain = float("inf")
    tied = False
    for candidate in strikes:
        total = 0.0
        for strike in strikes:
            ce = ce_oi.get(strike, 0.0)
            pe = pe_oi.get(strike, 0.0)
            total += ce * max(0.0, candidate - strike) + pe * max(
                0.0, strike - candidate
            )
        if total < best_pain:
            best_pain = total
            best_strike = candidate
            tied = False
        elif total == best_pain and best_strike is not None:
            tied = True
    if tied:
        return None
    return best_strike


def chain_metrics_from_quotes(
    quotes: dict[str, Any],
    *,
    find_row: Any,
    strikes: list[int],
    ce_symbols: list[str],
    pe_symbols: list[str],
) -> dict[str, float]:
    ce_oi_by_strike: dict[int, float] = {}
    pe_oi_by_strike: dict[int, float] = {}
    ce_oi = pe_oi = 0.0
    matched = 0

    for strike, ce_sym, pe_sym in zip(strikes, ce_symbols, pe_symbols, strict=False):
        ce_row = find_row(quotes, ce_sym)
        pe_row = find_row(quotes, pe_sym)
        if ce_row is not None or pe_row is not None:
            matched += 1
        ce = _oi(ce_row)
        pe = _oi(pe_row)
        ce_oi_by_strike[strike] = ce
        pe_oi_by_strike[strike] = pe
        ce_oi += ce
        pe_oi += pe

    # No quote rows at all → empty payload (callers treat as missing, not zeros).
    if matched == 0:
        return {}

    out: dict[str, float] = {}
    if pe_oi > 0:
        out["pcr"] = round(pe_oi / ce_oi, 3) if ce_oi > 0 else round(pe_oi, 3)
    max_pain = _max_pain_strike(strikes, ce_oi_by_strike, pe_oi_by_strike)
    if max_pain is not None:
        out["max_pain"] = float(max_pain)
    # Only publish OI totals when we actually saw interest — keeps UI "—" vs "0".
    if ce_oi > 0 or pe_oi > 0:
        out["chain_ce_oi"] = ce_oi
        out["chain_pe_oi"] = pe_oi
    grip = writer_grip_score(strikes, ce_oi_by_strike, pe_oi_by_strike)
    if grip is not None:
        out["writer_grip_score"] = grip
    return out


def writer_grip_score(
    strikes: list[int],
    ce_oi_by_strike: dict[int, float],
    pe_oi_by_strike: dict[int, float],
) -> float | None:
    """Share of chain OI concentrated at the single highest-OI strike (ATM writer grip)."""
    if not strikes:
        return None
    total = sum(ce_oi_by_strike.values()) + sum(pe_oi_by_strike.values())
    if total <= 0:
        return None
    peak = 0.0
    for strike in strikes:
        peak = max(peak, ce_oi_by_strike.get(strike, 0.0) + pe_oi_by_strike.get(strike, 0.0))
    return round(peak / total, 3)


def _oi(row: dict[str, Any] | None) -> float:
    if not row:
        return 0.0
    for key in ("open_interest", "oi"):
        val = row.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    return 0.0


def build_chain_symbols(
    fut_symbol: str,
    atm: int,
    strike_step: int,
    wings: int = 5,
) -> tuple[list[int], list[str], list[str]]:
    strikes = strike_ladder(atm, strike_step, wings)
    ce = _derive_option_symbols(fut_symbol, strikes, "CE")
    pe = _derive_option_symbols(fut_symbol, strikes, "PE")
    return strikes, ce, pe
