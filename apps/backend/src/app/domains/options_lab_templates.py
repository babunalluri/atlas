"""Minimal Options Lab strategy templates (strike geometry) for server bots.

Mirrors ungated templates in ``options-lab-strategy.ts`` ``buildStrategyFromTemplate``.
Premiums/symbols are filled later from the live chain.
"""

from __future__ import annotations

from typing import Any

# Calendar is gated on the desk until dual-expiry exists.
GATED_TEMPLATES = frozenset({"calendar_call"})

TEMPLATE_IDS = frozenset(
    {
        "long_ce",
        "long_pe",
        "short_ce",
        "short_pe",
        "long_straddle",
        "short_straddle",
        "long_strangle",
        "bull_call_spread",
        "bear_put_spread",
        "bull_put_spread",
        "bear_call_spread",
        "iron_condor",
        "iron_butterfly",
        "long_butterfly_ce",
        "call_ratio",
        "put_ratio",
        "calendar_call",
    }
)


def _leg(
    *,
    side: str,
    opt_type: str,
    strike: float,
    qty: float,
    index: int,
) -> dict[str, Any]:
    return {
        "id": f"tpl-{index}",
        "side": side,
        "type": opt_type,
        "strike": float(strike),
        "qty": float(qty),
        "premium": 0.0,
        "entry_premium": 0.0,
        "symbol": None,
        "unit": "lots",
    }


def build_template_legs(
    template_id: str,
    *,
    atm: float,
    strike_step: float,
    shift_steps: int = 0,
    width_steps: int = 1,
) -> list[dict[str, Any]]:
    """Return skeleton legs (no chain premiums) for ``template_id``."""
    tid = str(template_id or "").strip()
    if tid in GATED_TEMPLATES or tid not in TEMPLATE_IDS:
        return []
    step = max(1.0, float(strike_step or 1))
    center = float(atm) + int(shift_steps) * step
    width = max(1, int(width_steps)) * step

    if tid == "long_ce":
        return [_leg(side="buy", opt_type="CE", strike=center, qty=1, index=0)]
    if tid == "long_pe":
        return [_leg(side="buy", opt_type="PE", strike=center, qty=1, index=0)]
    if tid == "short_ce":
        return [_leg(side="sell", opt_type="CE", strike=center, qty=1, index=0)]
    if tid == "short_pe":
        return [_leg(side="sell", opt_type="PE", strike=center, qty=1, index=0)]
    if tid == "long_straddle":
        return [
            _leg(side="buy", opt_type="CE", strike=center, qty=1, index=0),
            _leg(side="buy", opt_type="PE", strike=center, qty=1, index=1),
        ]
    if tid == "short_straddle":
        return [
            _leg(side="sell", opt_type="CE", strike=center, qty=1, index=0),
            _leg(side="sell", opt_type="PE", strike=center, qty=1, index=1),
        ]
    if tid == "long_strangle":
        return [
            _leg(side="buy", opt_type="CE", strike=center + width, qty=1, index=0),
            _leg(side="buy", opt_type="PE", strike=center - width, qty=1, index=1),
        ]
    if tid == "bull_call_spread":
        return [
            _leg(side="buy", opt_type="CE", strike=center, qty=1, index=0),
            _leg(side="sell", opt_type="CE", strike=center + width, qty=1, index=1),
        ]
    if tid == "bear_put_spread":
        return [
            _leg(side="buy", opt_type="PE", strike=center, qty=1, index=0),
            _leg(side="sell", opt_type="PE", strike=center - width, qty=1, index=1),
        ]
    if tid == "bull_put_spread":
        return [
            _leg(side="sell", opt_type="PE", strike=center, qty=1, index=0),
            _leg(side="buy", opt_type="PE", strike=center - width, qty=1, index=1),
        ]
    if tid == "bear_call_spread":
        return [
            _leg(side="sell", opt_type="CE", strike=center, qty=1, index=0),
            _leg(side="buy", opt_type="CE", strike=center + width, qty=1, index=1),
        ]
    if tid == "iron_condor":
        return [
            _leg(side="sell", opt_type="PE", strike=center - width, qty=1, index=0),
            _leg(side="buy", opt_type="PE", strike=center - width * 2, qty=1, index=1),
            _leg(side="sell", opt_type="CE", strike=center + width, qty=1, index=2),
            _leg(side="buy", opt_type="CE", strike=center + width * 2, qty=1, index=3),
        ]
    if tid == "iron_butterfly":
        return [
            _leg(side="buy", opt_type="PE", strike=center - width, qty=1, index=0),
            _leg(side="sell", opt_type="PE", strike=center, qty=1, index=1),
            _leg(side="sell", opt_type="CE", strike=center, qty=1, index=2),
            _leg(side="buy", opt_type="CE", strike=center + width, qty=1, index=3),
        ]
    if tid == "long_butterfly_ce":
        return [
            _leg(side="buy", opt_type="CE", strike=center - width, qty=1, index=0),
            _leg(side="sell", opt_type="CE", strike=center, qty=2, index=1),
            _leg(side="buy", opt_type="CE", strike=center + width, qty=1, index=2),
        ]
    if tid == "call_ratio":
        return [
            _leg(side="buy", opt_type="CE", strike=center, qty=1, index=0),
            _leg(side="sell", opt_type="CE", strike=center + width, qty=2, index=1),
        ]
    if tid == "put_ratio":
        return [
            _leg(side="buy", opt_type="PE", strike=center, qty=1, index=0),
            _leg(side="sell", opt_type="PE", strike=center - width, qty=2, index=1),
        ]
    return []


def enrich_legs_from_chain(
    legs: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach symbol + mid/ltp premium from chain rows by strike/type."""
    by_strike: dict[float, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            strike = float(row.get("strike"))
        except (TypeError, ValueError):
            continue
        by_strike[strike] = row

    out: list[dict[str, Any]] = []
    for idx, leg in enumerate(legs):
        if not isinstance(leg, dict):
            continue
        try:
            strike = float(leg.get("strike"))
        except (TypeError, ValueError):
            continue
        opt_type = str(leg.get("type") or "CE").upper()
        row = by_strike.get(strike)
        side_row = None
        if isinstance(row, dict):
            side_row = row.get("ce" if opt_type == "CE" else "pe")
        premium = leg.get("premium")
        if premium is None:
            premium = leg.get("entry_premium")
        symbol = leg.get("symbol")
        if isinstance(side_row, dict):
            symbol = side_row.get("symbol") or symbol
            for key in ("ltp", "last_price", "close", "bid", "ask"):
                raw = side_row.get(key)
                if raw is None:
                    continue
                try:
                    val = float(raw)
                except (TypeError, ValueError):
                    continue
                if val > 0:
                    premium = val
                    break
            if (
                premium is None
                and side_row.get("bid") is not None
                and side_row.get("ask") is not None
            ):
                try:
                    bid = float(side_row["bid"])
                    ask = float(side_row["ask"])
                    if bid > 0 and ask > 0:
                        premium = (bid + ask) / 2.0
                except (TypeError, ValueError, KeyError):
                    pass
        try:
            qty = float(leg.get("qty") if leg.get("qty") is not None else 1)
        except (TypeError, ValueError):
            qty = 1.0
        try:
            prem_f = float(premium or 0)
        except (TypeError, ValueError):
            prem_f = 0.0
        unit = str(leg.get("unit") or "lots").lower()
        if unit not in {"lots", "shares"}:
            unit = "lots"
        out.append(
            {
                "id": str(leg.get("id") or f"leg-{idx}"),
                "side": str(leg.get("side") or "buy").lower(),
                "type": opt_type if opt_type in {"CE", "PE"} else "CE",
                "strike": strike,
                "qty": qty,
                "premium": prem_f,
                "entry_premium": prem_f,
                "symbol": str(symbol).strip() if symbol else None,
                "unit": unit,
            }
        )
    return out
