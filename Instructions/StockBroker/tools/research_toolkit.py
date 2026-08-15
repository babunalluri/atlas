"""Stock Broker research toolkit (Atlas tenant_python starter).

Compute-only: trend/MA/momentum/compare and defined F&O payoffs from
**caller-supplied** quotes, OHLC, strikes, and LTPs.

This toolkit does **not** call Groww, Kite, or any broker. The Research agent
must fetch live numbers via an assigned vendor toolkit (`get_ltp` / `get_quote`
/ `get_ohlc`) or take user-supplied prints, then pass those values here.

Groww/Kite adapters in this pack expose quotes for CASH and FUT/OPT symbols
when the user (or agent) already knows the trading symbol. They do **not**
expose a live option chain — do not invent one.

Bind on the Research team. Never mark these mutating. Never place orders.
"""

from __future__ import annotations

from typing import Any

STRUCTURES = (
    "long_call",
    "long_put",
    "covered_call",
    "bull_call_spread",
    "iron_condor",
)

_CHAIN_NOTE = (
    "Live option chain is not available from Groww/Kite quote tools in this pack. "
    "Strikes and premiums must come from get_ltp/get_quote (known symbols) or the user. "
    "Do not invent a chain, IV, or Greeks."
)


def _ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data}


def _err(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def _f(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    return float(value)


def _parse_series(raw: str | list[Any] | tuple[Any, ...] | None) -> list[float]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, (list, tuple)):
        out: list[float] = []
        for item in raw:
            try:
                out.append(float(item))
            except (TypeError, ValueError):
                continue
        return out
    parts = [p.strip() for p in str(raw).replace(";", ",").split(",")]
    out = []
    for part in parts:
        if not part:
            continue
        try:
            out.append(float(part))
        except ValueError:
            continue
    return out


def _sma(values: list[float], window: int) -> float | None:
    if window < 1 or len(values) < window:
        return None
    chunk = values[-window:]
    return sum(chunk) / window


def _round_money(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 4)


def compute_stock_snapshot(
    *,
    symbol: str,
    last_price: float = 0,
    open_price: float = 0,
    high: float = 0,
    low: float = 0,
    previous_close: float = 0,
    closes: list[float] | None = None,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
) -> dict[str, Any]:
    """Trend / MA / momentum / crude S-R from supplied prints only."""
    symbol = (symbol or "").strip()
    if not symbol:
        return _err("symbol is required")

    series = list(closes or [])
    high_series = list(highs or [])
    low_series = list(lows or [])
    ltp = _f(last_price)
    if ltp <= 0 and series:
        ltp = series[-1]
    if ltp <= 0 and _f(close := previous_close) > 0:
        ltp = close
    if ltp <= 0:
        return _err(
            "No last price. Pass last_price or closes from get_ltp/get_quote/get_ohlc "
            "(or the user). Do not invent a quote."
        )

    o = _f(open_price)
    h = _f(high)
    lo = _f(low)
    prev = _f(previous_close)
    if h <= 0 and high_series:
        h = max(high_series)
    if lo <= 0 and low_series:
        lo = min(low_series)

    change = None
    change_pct = None
    if prev > 0:
        change = ltp - prev
        change_pct = (change / prev) * 100
    elif o > 0:
        change = ltp - o
        change_pct = (change / o) * 100

    sma5 = _sma(series, 5) if len(series) >= 5 else _sma(series, len(series)) if len(series) >= 2 else None
    sma20 = _sma(series, 20)

    if sma5 is not None and ltp > sma5:
        ma_bias = "above_short_ma"
    elif sma5 is not None and ltp < sma5:
        ma_bias = "below_short_ma"
    else:
        ma_bias = "insufficient_series_for_ma"

    if o > 0 and ltp > o:
        candle = "bullish"
    elif o > 0 and ltp < o:
        candle = "bearish"
    elif o > 0:
        candle = "doji"
    else:
        candle = "unknown"

    if change is not None and change > 0:
        momentum = "up"
    elif change is not None and change < 0:
        momentum = "down"
    else:
        momentum = "flat_or_unknown"

    support = lo if lo > 0 else (min(low_series) if low_series else None)
    resistance = h if h > 0 else (max(high_series) if high_series else None)
    if low_series and len(low_series) >= 3:
        support = min(low_series)
    if high_series and len(high_series) >= 3:
        resistance = max(high_series)

    notes: list[str] = []
    if len(series) < 5:
        notes.append(
            "SMA/trend from a full series needs recent closes (5+). "
            "Assigned Groww/Kite tools expose current quote/OHLC, not a historical candle API. "
            "Do not invent candles."
        )
    if not high_series and not low_series and (h <= 0 or lo <= 0):
        notes.append("Support/resistance is limited without highs/lows from OHLC or a candle series.")

    return _ok(
        {
            "symbol": symbol,
            "last_price": _round_money(ltp),
            "open": _round_money(o) if o else None,
            "high": _round_money(h) if h else None,
            "low": _round_money(lo) if lo else None,
            "previous_close": _round_money(prev) if prev else None,
            "change": _round_money(change),
            "change_pct": _round_money(change_pct),
            "intraday_candle": candle,
            "momentum": momentum,
            "sma5": _round_money(sma5),
            "sma20": _round_money(sma20),
            "ma_bias": ma_bias,
            "support": _round_money(support),
            "resistance": _round_money(resistance),
            "closes_used": len(series),
            "notes": notes,
            "disclaimer": "Computed from supplied prints only. Not a forecast. Not investment advice.",
        }
    )


def compute_compare_symbols(
    *,
    symbol_a: str,
    ltp_a: float,
    symbol_b: str,
    ltp_b: float,
    change_pct_a: float = 0,
    change_pct_b: float = 0,
    previous_close_a: float = 0,
    previous_close_b: float = 0,
) -> dict[str, Any]:
    a = (symbol_a or "").strip()
    b = (symbol_b or "").strip()
    if not a or not b:
        return _err("symbol_a and symbol_b are required")
    pa = _f(ltp_a)
    pb = _f(ltp_b)
    if pa <= 0 or pb <= 0:
        return _err(
            "Both last prices are required from get_ltp/get_quote (or the user). "
            "Do not invent a comparison."
        )
    pct_a = _f(change_pct_a)
    pct_b = _f(change_pct_b)
    prev_a = _f(previous_close_a)
    prev_b = _f(previous_close_b)
    if pct_a == 0 and prev_a > 0:
        pct_a = ((pa - prev_a) / prev_a) * 100
    if pct_b == 0 and prev_b > 0:
        pct_b = ((pb - prev_b) / prev_b) * 100

    if pct_a > pct_b:
        stronger = a
    elif pct_b > pct_a:
        stronger = b
    else:
        stronger = "tied_or_unknown_change"

    return _ok(
        {
            "a": {"symbol": a, "last_price": _round_money(pa), "change_pct": _round_money(pct_a)},
            "b": {"symbol": b, "last_price": _round_money(pb), "change_pct": _round_money(pct_b)},
            "ltp_ratio_a_over_b": _round_money(pa / pb),
            "change_pct_spread": _round_money(pct_a - pct_b),
            "stronger_today": stronger,
            "disclaimer": "Relative snapshot from supplied prints. Not a pair-trade recommendation.",
        }
    )


def _call_payoff(spot: float, strike: float, premium: float, sign: int) -> float:
    return sign * (max(spot - strike, 0.0) - premium)


def _put_payoff(spot: float, strike: float, premium: float, sign: int) -> float:
    return sign * (max(strike - spot, 0.0) - premium)


def _stock_payoff(spot: float, entry: float, sign: int) -> float:
    return sign * (spot - entry)


def payoff_at_spot(structure: str, inputs: dict[str, float], spot: float) -> float:
    name = structure.strip().lower()
    if name == "long_call":
        return _call_payoff(spot, inputs["strike"], inputs["premium"], 1)
    if name == "long_put":
        return _put_payoff(spot, inputs["strike"], inputs["premium"], 1)
    if name == "covered_call":
        return _stock_payoff(spot, inputs["stock_entry"], 1) + _call_payoff(
            spot, inputs["strike"], inputs["premium"], -1
        )
    if name == "bull_call_spread":
        return _call_payoff(spot, inputs["long_strike"], inputs["long_premium"], 1) + _call_payoff(
            spot, inputs["short_strike"], inputs["short_premium"], -1
        )
    if name == "iron_condor":
        return (
            _put_payoff(spot, inputs["long_put_strike"], inputs["long_put_premium"], 1)
            + _put_payoff(spot, inputs["short_put_strike"], inputs["short_put_premium"], -1)
            + _call_payoff(spot, inputs["short_call_strike"], inputs["short_call_premium"], -1)
            + _call_payoff(spot, inputs["long_call_strike"], inputs["long_call_premium"], 1)
        )
    raise ValueError(f"unsupported structure: {structure}")


def _require_positive(inputs: dict[str, float], *keys: str) -> str | None:
    missing = [key for key in keys if inputs.get(key, 0) <= 0]
    if missing:
        return (
            "Missing or non-positive: "
            + ", ".join(missing)
            + ". Fetch LTP/strike via assigned quote tools or ask the user. Do not invent prices."
        )
    return None


def analyze_option_payoff(
    *,
    structure: str,
    quantity: int = 1,
    lot_size: int = 1,
    strike: float = 0,
    premium: float = 0,
    stock_entry: float = 0,
    long_strike: float = 0,
    long_premium: float = 0,
    short_strike: float = 0,
    short_premium: float = 0,
    long_put_strike: float = 0,
    long_put_premium: float = 0,
    short_put_strike: float = 0,
    short_put_premium: float = 0,
    short_call_strike: float = 0,
    short_call_premium: float = 0,
    long_call_strike: float = 0,
    long_call_premium: float = 0,
    spots: list[float] | None = None,
) -> dict[str, Any]:
    name = (structure or "").strip().lower()
    if name not in STRUCTURES:
        return _err(
            f"structure must be one of: {', '.join(STRUCTURES)}. "
            "This is a defined-structure calculator, not every possible strategy."
        )
    qty = int(quantity or 1)
    lot = int(lot_size or 1)
    if qty <= 0 or lot <= 0:
        return _err("quantity and lot_size must be positive")
    multiplier = qty * lot

    raw = {
        "strike": _f(strike),
        "premium": _f(premium),
        "stock_entry": _f(stock_entry),
        "long_strike": _f(long_strike),
        "long_premium": _f(long_premium),
        "short_strike": _f(short_strike),
        "short_premium": _f(short_premium),
        "long_put_strike": _f(long_put_strike),
        "long_put_premium": _f(long_put_premium),
        "short_put_strike": _f(short_put_strike),
        "short_put_premium": _f(short_put_premium),
        "short_call_strike": _f(short_call_strike),
        "short_call_premium": _f(short_call_premium),
        "long_call_strike": _f(long_call_strike),
        "long_call_premium": _f(long_call_premium),
    }

    if name in {"long_call", "long_put"}:
        problem = _require_positive(raw, "strike", "premium")
    elif name == "covered_call":
        problem = _require_positive(raw, "strike", "premium", "stock_entry")
    elif name == "bull_call_spread":
        problem = _require_positive(raw, "long_strike", "long_premium", "short_strike", "short_premium")
        if not problem and raw["long_strike"] >= raw["short_strike"]:
            problem = "bull_call_spread needs long_strike < short_strike"
    elif name == "iron_condor":
        problem = _require_positive(
            raw,
            "long_put_strike",
            "long_put_premium",
            "short_put_strike",
            "short_put_premium",
            "short_call_strike",
            "short_call_premium",
            "long_call_strike",
            "long_call_premium",
        )
        if not problem and not (
            raw["long_put_strike"] < raw["short_put_strike"] < raw["short_call_strike"] < raw["long_call_strike"]
        ):
            problem = (
                "iron_condor needs long_put_strike < short_put_strike < "
                "short_call_strike < long_call_strike"
            )
    else:
        problem = "unsupported structure"
    if problem:
        return _err(problem)

    if name == "long_call":
        max_loss = raw["premium"]
        max_profit = None
        breakevens = [raw["strike"] + raw["premium"]]
        summary = "Long call: unlimited upside, loss limited to premium paid."
    elif name == "long_put":
        max_loss = raw["premium"]
        max_profit = raw["strike"] - raw["premium"]
        breakevens = [raw["strike"] - raw["premium"]]
        summary = "Long put: profit if spot falls below strike minus premium."
    elif name == "covered_call":
        max_profit = (raw["strike"] - raw["stock_entry"]) + raw["premium"]
        max_loss = raw["stock_entry"] - raw["premium"]
        breakevens = [raw["stock_entry"] - raw["premium"]]
        summary = "Covered call: long stock + short call. Upside capped at strike."
    elif name == "bull_call_spread":
        net = raw["long_premium"] - raw["short_premium"]
        width = raw["short_strike"] - raw["long_strike"]
        max_loss = net
        max_profit = width - net
        breakevens = [raw["long_strike"] + net]
        summary = "Bull call spread: debit. Profit if spot rises toward the short strike."
    else:
        credit = (
            raw["short_put_premium"]
            + raw["short_call_premium"]
            - raw["long_put_premium"]
            - raw["long_call_premium"]
        )
        put_width = raw["short_put_strike"] - raw["long_put_strike"]
        call_width = raw["long_call_strike"] - raw["short_call_strike"]
        max_profit = credit
        max_loss = max(put_width, call_width) - credit
        breakevens = [
            raw["short_put_strike"] - credit,
            raw["short_call_strike"] + credit,
        ]
        summary = "Iron condor: credit. Max profit if spot stays between the short strikes."

    eval_spots = list(spots or [])
    if not eval_spots:
        if name == "iron_condor":
            eval_spots = [
                raw["long_put_strike"],
                raw["short_put_strike"],
                (raw["short_put_strike"] + raw["short_call_strike"]) / 2,
                raw["short_call_strike"],
                raw["long_call_strike"],
            ]
        elif name == "bull_call_spread":
            eval_spots = [raw["long_strike"], breakevens[0], raw["short_strike"]]
        elif name == "covered_call":
            eval_spots = [0.0, breakevens[0], raw["stock_entry"], raw["strike"]]
        else:
            eval_spots = [max(raw["strike"] * 0.9, 0.0), raw["strike"], raw["strike"] * 1.1]

    payoff_rows = [
        {
            "spot": _round_money(spot),
            "pnl_per_unit": _round_money(payoff_at_spot(name, raw, spot)),
            "pnl": _round_money(payoff_at_spot(name, raw, spot) * multiplier),
        }
        for spot in eval_spots
    ]

    used = {key: _round_money(value) for key, value in raw.items() if value}
    return _ok(
        {
            "structure": name,
            "quantity": qty,
            "lot_size": lot,
            "multiplier": multiplier,
            "inputs": used,
            "max_profit": None if max_profit is None else _round_money(max_profit * multiplier),
            "max_profit_per_unit": None if max_profit is None else _round_money(max_profit),
            "max_loss": _round_money(max_loss * multiplier),
            "max_loss_per_unit": _round_money(max_loss),
            "breakevens": [_round_money(level) for level in breakevens],
            "unlimited_profit": max_profit is None,
            "payoff_at_spots": payoff_rows,
            "summary": summary,
            "notes": [_CHAIN_NOTE],
            "disclaimer": (
                "Theoretical payoff from supplied strikes/LTPs only. "
                "Ignores slippage, taxes, and margin. Not a live chain. Not investment advice."
            ),
        }
    )


# --- Atlas tool surface (names the Research agent must pick) ---


async def research_stock_snapshot(
    ctx,
    symbol: str,
    last_price: float = 0,
    open_price: float = 0,
    high: float = 0,
    low: float = 0,
    previous_close: float = 0,
    closes: str = "",
    highs: str = "",
    lows: str = "",
) -> dict[str, Any]:
    """Stock trend/MA/momentum/S-R from supplied quote or OHLC. Fetch prints first."""
    del ctx
    return compute_stock_snapshot(
        symbol=symbol,
        last_price=last_price,
        open_price=open_price,
        high=high,
        low=low,
        previous_close=previous_close,
        closes=_parse_series(closes),
        highs=_parse_series(highs),
        lows=_parse_series(lows),
    )


async def research_compare_symbols(
    ctx,
    symbol_a: str,
    ltp_a: float,
    symbol_b: str,
    ltp_b: float,
    change_pct_a: float = 0,
    change_pct_b: float = 0,
    previous_close_a: float = 0,
    previous_close_b: float = 0,
) -> dict[str, Any]:
    """Compare two symbols from supplied last prices. Fetch both quotes first."""
    del ctx
    return compute_compare_symbols(
        symbol_a=symbol_a,
        ltp_a=ltp_a,
        symbol_b=symbol_b,
        ltp_b=ltp_b,
        change_pct_a=change_pct_a,
        change_pct_b=change_pct_b,
        previous_close_a=previous_close_a,
        previous_close_b=previous_close_b,
    )


async def research_option_payoff(
    ctx,
    structure: str,
    quantity: int = 1,
    lot_size: int = 1,
    strike: float = 0,
    premium: float = 0,
    stock_entry: float = 0,
    long_strike: float = 0,
    long_premium: float = 0,
    short_strike: float = 0,
    short_premium: float = 0,
    long_put_strike: float = 0,
    long_put_premium: float = 0,
    short_put_strike: float = 0,
    short_put_premium: float = 0,
    short_call_strike: float = 0,
    short_call_premium: float = 0,
    long_call_strike: float = 0,
    long_call_premium: float = 0,
    spots: str = "",
) -> dict[str, Any]:
    """Payoff / breakeven / max loss for a defined F&O structure from supplied strikes/LTP.

    structures: long_call, long_put, covered_call, bull_call_spread, iron_condor.
    Does not list or invent an option chain.
    """
    del ctx
    return analyze_option_payoff(
        structure=structure,
        quantity=quantity,
        lot_size=lot_size,
        strike=strike,
        premium=premium,
        stock_entry=stock_entry,
        long_strike=long_strike,
        long_premium=long_premium,
        short_strike=short_strike,
        short_premium=short_premium,
        long_put_strike=long_put_strike,
        long_put_premium=long_put_premium,
        short_put_strike=short_put_strike,
        short_put_premium=short_put_premium,
        short_call_strike=short_call_strike,
        short_call_premium=short_call_premium,
        long_call_strike=long_call_strike,
        long_call_premium=long_call_premium,
        spots=_parse_series(spots),
    )
