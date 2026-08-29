"""Options Lab model backtests — tenant session store + synthetic √t path runner.

Fidelity:
  ``model`` — expiry intrinsic vs synthetic spot path
  ``model_hist`` — expiry intrinsic vs historical closes
  ``bs_marks`` — Black-76 mid marks along a spot path (not NSE tick archive)
"""

from __future__ import annotations

import math
import time
import uuid
from typing import Any

from app.domains.signal_engine_cache import get_session_value, set_session_value

BACKTESTS_FIELD = "options_lab:backtests"
MAX_BACKTESTS = 24
MAX_DAYS = 45
MIN_DAYS = 3


def _now_ts() -> int:
    return int(time.time())


def _leg_payoff_at_spot(
    *,
    side: str,
    opt_type: str,
    strike: float,
    qty: float,
    premium: float,
    spot: float,
) -> float:
    """Expiry intrinsic P&L per leg (same class as Lab expiry curve)."""
    ce = str(opt_type or "").upper() == "CE"
    intrinsic = max(0.0, spot - strike) if ce else max(0.0, strike - spot)
    is_buy = str(side or "buy").lower() == "buy"
    unit = (intrinsic - premium) if is_buy else (premium - intrinsic)
    return unit * float(qty or 1)


def strategy_pnl_at_spot(legs: list[dict[str, Any]], spot: float) -> float:
    total = 0.0
    for leg in legs:
        try:
            strike = float(leg.get("strike"))
            premium = float(
                leg.get("premium")
                if leg.get("premium") is not None
                else leg.get("entry_premium") or 0
            )
            qty = float(leg.get("qty") if leg.get("qty") is not None else 1)
        except (TypeError, ValueError):
            continue
        total += _leg_payoff_at_spot(
            side=str(leg.get("side") or "buy"),
            opt_type=str(leg.get("type") or "CE"),
            strike=strike,
            qty=qty,
            premium=premium,
            spot=spot,
        )
    return total


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black76_price(
    *,
    forward: float,
    strike: float,
    years: float,
    sigma: float,
    opt_type: str,
) -> float:
    """Black-76 mid (r=0) — index F&O style. ``years <= 0`` → intrinsic."""
    ce = str(opt_type or "").upper() == "CE"
    if not (forward > 0 and strike > 0):
        return 0.0
    if years <= 0 or not (sigma > 0):
        return max(0.0, forward - strike) if ce else max(0.0, strike - forward)
    vol = sigma * math.sqrt(years)
    if vol < 1e-12:
        return max(0.0, forward - strike) if ce else max(0.0, strike - forward)
    d1 = (math.log(forward / strike) + 0.5 * sigma * sigma * years) / vol
    d2 = d1 - vol
    if ce:
        return forward * _norm_cdf(d1) - strike * _norm_cdf(d2)
    return strike * _norm_cdf(-d2) - forward * _norm_cdf(-d1)


def strategy_pnl_at_bs_mark(
    legs: list[dict[str, Any]],
    *,
    spot: float,
    years: float,
    iv_pct: float,
) -> float:
    """Mark-to-model P&L vs entry premium (Black-76 mid, flat IV)."""
    sigma = max(0.01, float(iv_pct) / 100.0)
    total = 0.0
    for leg in legs:
        try:
            strike = float(leg.get("strike"))
            premium = float(
                leg.get("premium")
                if leg.get("premium") is not None
                else leg.get("entry_premium") or 0
            )
            qty = float(leg.get("qty") if leg.get("qty") is not None else 1)
        except (TypeError, ValueError):
            continue
        mark = black76_price(
            forward=spot,
            strike=strike,
            years=max(0.0, years),
            sigma=sigma,
            opt_type=str(leg.get("type") or "CE"),
        )
        is_buy = str(leg.get("side") or "buy").lower() == "buy"
        unit = (mark - premium) if is_buy else (premium - mark)
        total += unit * qty
    return total


def _synthetic_path(
    *,
    spot: float,
    days_n: int,
    shock: float,
    bias: str,
) -> list[float]:
    path: list[float] = []
    for i in range(days_n):
        sqrt_t = math.sqrt(i + 1)
        move = (shock / 100.0) * sqrt_t
        if bias == "up":
            path.append(spot * (1 + move * 0.85))
        elif bias == "down":
            path.append(spot * (1 - move * 0.85))
        else:
            path.append(
                spot * (1 + math.sin(((i + 1) / days_n) * math.pi * 2) * move * 0.35)
            )
    return path


def run_bs_mark_backtest(
    *,
    legs: list[dict[str, Any]],
    spot: float,
    days: int = 10,
    shock_pct: float = 2.0,
    path_bias: str = "flat",
    iv_pct: float = 15.0,
    entry_dte: float | None = None,
    closes: list[float] | None = None,
) -> dict[str, Any] | None:
    """Black-76 marks along synthetic or historical spot path (fidelity bs_marks)."""
    if not legs or not math.isfinite(spot) or spot <= 0:
        return None
    shock = max(0.5, min(15.0, float(shock_pct or 2.0)))
    bias = str(path_bias or "flat").lower()
    if bias not in {"up", "down", "flat", "historical"}:
        bias = "flat"
    iv = max(1.0, min(120.0, float(iv_pct or 15.0)))
    dte0 = float(entry_dte) if entry_dte is not None else None
    if dte0 is None or not math.isfinite(dte0) or dte0 <= 0:
        dte0 = float(days or 10)

    # Horizon is the backtest window, capped by remaining life so we don't mark past expiry.
    horizon = max(MIN_DAYS, min(MAX_DAYS, int(days or 10)))
    life_days = max(1, int(math.ceil(dte0)))
    days_n = min(horizon, life_days)

    if closes:
        path = [float(c) for c in closes if isinstance(c, (int, float)) and float(c) > 0]
        path = path[-MAX_DAYS:]
        if len(path) < MIN_DAYS:
            return None
        path = path[:days_n] if len(path) > days_n else path
        if len(path) < MIN_DAYS:
            return None
        spot0 = path[0]
        bias = "historical"
    else:
        path = _synthetic_path(spot=spot, days_n=days_n, shock=shock, bias=bias)
        spot0 = spot

    shocks: list[dict[str, Any]] = []
    for i, path_spot in enumerate(path):
        day = i + 1
        raw_years = (dte0 - day) / 365.0
        if raw_years <= 0:
            years = 0.0  # intrinsic at/after expiry
        elif raw_years < 0.0005:
            years = 0.0005  # ~4h numerical floor — still before expiry
        else:
            years = raw_years
        move = (shock / 100.0) * math.sqrt(day)
        up = spot0 * (1 + move)
        down = spot0 * (1 - move)
        shocks.append(
            {
                "day": day,
                "up": round(up, 4),
                "down": round(down, 4),
                "path_spot": round(path_spot, 4),
                "pnl_up": round(
                    strategy_pnl_at_bs_mark(legs, spot=up, years=years, iv_pct=iv), 4
                ),
                "pnl_down": round(
                    strategy_pnl_at_bs_mark(legs, spot=down, years=years, iv_pct=iv), 4
                ),
                "pnl_path": round(
                    strategy_pnl_at_bs_mark(
                        legs, spot=path_spot, years=years, iv_pct=iv
                    ),
                    4,
                ),
            }
        )
    pnls = [s["pnl_up"] for s in shocks] + [s["pnl_down"] for s in shocks]
    path_pnls = [s["pnl_path"] for s in shocks]
    wins = sum(1 for p in pnls if p > 0)
    avg = sum(pnls) / len(pnls) if pnls else 0.0
    return {
        "fidelity": "bs_marks",
        "days": len(shocks),
        "shock_pct": shock,
        "path_bias": bias,
        "spot": spot0,
        "iv_pct": iv,
        "entry_dte": dte0,
        "shocks": shocks,
        "equity": [{"day": s["day"], "equity": s["pnl_path"]} for s in shocks],
        "stats": {
            "hit_rate": round(wins / len(pnls), 4) if pnls else 0.0,
            "avg_pnl": round(avg, 4),
            "path_trough": round(min(path_pnls), 4) if path_pnls else 0.0,
            "path_peak": round(max(path_pnls), 4) if path_pnls else 0.0,
        },
        "note": (
            "Path MTM via Black-76 mid (flat IV) — not live chain marks or option tick replay."
        ),
    }


def run_model_backtest(
    *,
    legs: list[dict[str, Any]],
    spot: float,
    days: int = 10,
    shock_pct: float = 2.0,
    path_bias: str = "flat",
) -> dict[str, Any] | None:
    """Synthetic √t path model — mirrors OptionsLabBacktestPanel math."""
    if not legs or not math.isfinite(spot) or spot <= 0:
        return None
    days_n = max(MIN_DAYS, min(MAX_DAYS, int(days or 10)))
    shock = max(0.5, min(15.0, float(shock_pct or 2.0)))
    bias = str(path_bias or "flat").lower()
    if bias not in {"up", "down", "flat"}:
        bias = "flat"

    shocks: list[dict[str, Any]] = []
    for i in range(days_n):
        sqrt_t = math.sqrt(i + 1)
        move = (shock / 100.0) * sqrt_t
        up = spot * (1 + move)
        down = spot * (1 - move)
        if bias == "up":
            path_spot = spot * (1 + move * 0.85)
        elif bias == "down":
            path_spot = spot * (1 - move * 0.85)
        else:
            path_spot = spot * (
                1 + math.sin(((i + 1) / days_n) * math.pi * 2) * move * 0.35
            )
        shocks.append(
            {
                "day": i + 1,
                "up": round(up, 4),
                "down": round(down, 4),
                "path_spot": round(path_spot, 4),
                "pnl_up": round(strategy_pnl_at_spot(legs, up), 4),
                "pnl_down": round(strategy_pnl_at_spot(legs, down), 4),
                "pnl_path": round(strategy_pnl_at_spot(legs, path_spot), 4),
            }
        )

    pnls = [s["pnl_up"] for s in shocks] + [s["pnl_down"] for s in shocks]
    path_pnls = [s["pnl_path"] for s in shocks]
    wins = sum(1 for p in pnls if p > 0)
    avg = sum(pnls) / len(pnls) if pnls else 0.0
    trough = min(path_pnls) if path_pnls else 0.0
    peak = max(path_pnls) if path_pnls else 0.0
    equity = [{"day": s["day"], "equity": s["pnl_path"]} for s in shocks]

    return {
        "fidelity": "model",
        "days": days_n,
        "shock_pct": shock,
        "path_bias": bias,
        "spot": spot,
        "shocks": shocks,
        "equity": equity,
        "stats": {
            "hit_rate": round(wins / len(pnls), 4) if pnls else 0.0,
            "avg_pnl": round(avg, 4),
            "path_trough": round(trough, 4),
            "path_peak": round(peak, 4),
        },
    }


def run_historical_close_backtest(
    *,
    legs: list[dict[str, Any]],
    closes: list[float],
    shock_pct: float = 2.0,
) -> dict[str, Any] | None:
    """Model P&L along real daily closes (still expiry-intrinsic; fidelity model_hist)."""
    path = [float(c) for c in closes if isinstance(c, (int, float)) and float(c) > 0]
    if not legs or len(path) < MIN_DAYS:
        return None
    path = path[-MAX_DAYS:]
    spot0 = path[0]
    shock = max(0.5, min(15.0, float(shock_pct or 2.0)))
    shocks: list[dict[str, Any]] = []
    for i, path_spot in enumerate(path):
        day = i + 1
        move = (shock / 100.0) * math.sqrt(day)
        up = spot0 * (1 + move)
        down = spot0 * (1 - move)
        shocks.append(
            {
                "day": day,
                "up": round(up, 4),
                "down": round(down, 4),
                "path_spot": round(path_spot, 4),
                "pnl_up": round(strategy_pnl_at_spot(legs, up), 4),
                "pnl_down": round(strategy_pnl_at_spot(legs, down), 4),
                "pnl_path": round(strategy_pnl_at_spot(legs, path_spot), 4),
            }
        )
    pnls = [s["pnl_up"] for s in shocks] + [s["pnl_down"] for s in shocks]
    path_pnls = [s["pnl_path"] for s in shocks]
    wins = sum(1 for p in pnls if p > 0)
    avg = sum(pnls) / len(pnls) if pnls else 0.0
    return {
        "fidelity": "model_hist",
        "days": len(shocks),
        "shock_pct": shock,
        "path_bias": "historical",
        "spot": spot0,
        "shocks": shocks,
        "equity": [{"day": s["day"], "equity": s["pnl_path"]} for s in shocks],
        "stats": {
            "hit_rate": round(wins / len(pnls), 4) if pnls else 0.0,
            "avg_pnl": round(avg, 4),
            "path_trough": round(min(path_pnls), 4) if path_pnls else 0.0,
            "path_peak": round(max(path_pnls), 4) if path_pnls else 0.0,
        },
        "note": "Path uses historical closes; P&L is still expiry-intrinsic model (not chain marks).",
    }


def normalize_leg(raw: dict[str, Any], *, index: int) -> dict[str, Any] | None:
    side = str(raw.get("side") or "").lower()
    opt_type = str(raw.get("type") or "").upper()
    if side not in {"buy", "sell"} or opt_type not in {"CE", "PE"}:
        return None
    try:
        strike = float(raw.get("strike"))
        qty = float(raw.get("qty") if raw.get("qty") is not None else 1)
        premium = float(
            raw.get("premium")
            if raw.get("premium") is not None
            else raw.get("entry_premium") or 0
        )
    except (TypeError, ValueError):
        return None
    if strike <= 0 or qty == 0:
        return None
    # Backtest P&L treats qty as a plain lots multiplier — reject share qty.
    unit = str(raw.get("unit") or "lots").lower()
    if unit == "shares":
        return None
    if unit not in {"lots", "shares"}:
        unit = "lots"
    return {
        "id": str(raw.get("id") or f"leg-{index}"),
        "side": side,
        "type": opt_type,
        "strike": int(strike) if float(strike).is_integer() else strike,
        "qty": qty,
        "premium": premium,
        "symbol": str(raw.get("symbol") or "").strip() or None,
        "unit": "lots",
    }


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if den_x < 1e-12 or den_y < 1e-12:
        return None
    return num / (den_x * den_y)


def portfolio_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    """OA-inspired multi-run summary — averages + optional path correlation."""
    runs = [r for r in items if isinstance(r, dict) and isinstance(r.get("result"), dict)]
    if not runs:
        return {"ok": True, "count": 0, "runs": [], "stats": None}

    hit_rates: list[float] = []
    avgs: list[float] = []
    troughs: list[float] = []
    peaks: list[float] = []
    cards: list[dict[str, Any]] = []
    for row in runs:
        result = row["result"]
        stats = result.get("stats") if isinstance(result.get("stats"), dict) else {}
        hr = float(stats.get("hit_rate") or 0)
        avg = float(stats.get("avg_pnl") or 0)
        trough = float(stats.get("path_trough") or 0)
        peak = float(stats.get("path_peak") or 0)
        hit_rates.append(hr)
        avgs.append(avg)
        troughs.append(trough)
        peaks.append(peak)
        cards.append(
            {
                "id": row.get("id"),
                "name": row.get("name"),
                "hit_rate": hr,
                "avg_pnl": avg,
                "path_trough": trough,
                "path_peak": peak,
                "days": result.get("days"),
                "fidelity": result.get("fidelity") or row.get("fidelity") or "model",
            }
        )

    correlations: list[dict[str, Any]] = []
    for i in range(len(runs)):
        for j in range(i + 1, len(runs)):
            ei = [
                float(p.get("equity") or 0)
                for p in (runs[i]["result"].get("equity") or [])
                if isinstance(p, dict)
            ]
            ej = [
                float(p.get("equity") or 0)
                for p in (runs[j]["result"].get("equity") or [])
                if isinstance(p, dict)
            ]
            if len(ei) < 3 or len(ei) != len(ej):
                continue
            corr = _pearson(ei, ej)
            if corr is None:
                continue
            correlations.append(
                {
                    "a": runs[i].get("id"),
                    "b": runs[j].get("id"),
                    "a_name": runs[i].get("name"),
                    "b_name": runs[j].get("name"),
                    "corr": round(corr, 4),
                }
            )

    n = len(runs)
    return {
        "ok": True,
        "count": n,
        "runs": cards,
        "stats": {
            "avg_hit_rate": round(sum(hit_rates) / n, 4),
            "avg_pnl": round(sum(avgs) / n, 4),
            "avg_path_trough": round(sum(troughs) / n, 4),
            "avg_path_peak": round(sum(peaks) / n, 4),
        },
        "correlations": correlations,
        "fidelity": "model",
        "note": "Summary of saved model runs — not a live portfolio optimizer.",
    }


def owned_by(row: dict[str, Any], owner_id: str | None) -> bool:
    """True when ``owner_id`` may see and act on this row.

    ``None`` is the operator scope (the whole tenant). A trader passes their
    user id and sees only their own. Rows written before ownership existed
    carry no ``owner_id``; they were all created while these routes were
    admin-only, so they stay operator-visible.
    """
    if owner_id is None:
        return True
    return str(row.get("owner_id") or "") == owner_id


def visible_rows(
    rows: list[dict[str, Any]], owner_id: str | None
) -> list[dict[str, Any]]:
    return [row for row in rows if owned_by(row, owner_id)]


async def load_backtests(tenant_id: str) -> list[dict[str, Any]]:
    stored = await get_session_value(tenant_id, BACKTESTS_FIELD)
    if not isinstance(stored, dict):
        return []
    rows = stored.get("items")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


async def save_backtests(tenant_id: str, items: list[dict[str, Any]]) -> None:
    await set_session_value(
        tenant_id,
        BACKTESTS_FIELD,
        {"items": items[:MAX_BACKTESTS], "updated_at": _now_ts()},
    )


async def list_backtests(
    tenant_id: str, *, owner_id: str | None = None
) -> dict[str, Any]:
    items = visible_rows(await load_backtests(tenant_id), owner_id)
    slim: list[dict[str, Any]] = []
    for row in items:
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        slim.append(
            {
                "id": row.get("id"),
                "name": row.get("name"),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
                "fidelity": row.get("fidelity") or result.get("fidelity") or "model",
                "underlying_symbol": row.get("underlying_symbol"),
                "params": row.get("params"),
                "stats": result.get("stats"),
                "leg_count": len(row.get("legs") or []),
            }
        )
    return {"ok": True, "backtests": slim, "count": len(slim)}


async def get_backtest(
    tenant_id: str, backtest_id: str, *, owner_id: str | None = None
) -> dict[str, Any]:
    for row in await load_backtests(tenant_id):
        if row.get("id") == backtest_id:
            # Another trader's backtest reads as absent, never as forbidden.
            if not owned_by(row, owner_id):
                break
            return {"ok": True, "backtest": row}
    return {"ok": False, "error": "Backtest not found."}


async def delete_backtest(
    tenant_id: str, backtest_id: str, *, owner_id: str | None = None
) -> dict[str, Any]:
    items = await load_backtests(tenant_id)
    next_items = [
        row
        for row in items
        if not (row.get("id") == backtest_id and owned_by(row, owner_id))
    ]
    if len(next_items) == len(items):
        return {"ok": False, "error": "Backtest not found."}
    await save_backtests(tenant_id, next_items)
    return {"ok": True}


async def create_backtest(
    tenant_id: str, payload: dict[str, Any], *, owner_id: str | None = None
) -> dict[str, Any]:
    raw_legs = payload.get("legs") if isinstance(payload.get("legs"), list) else []
    legs: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_legs):
        if isinstance(item, dict):
            leg = normalize_leg(item, index=idx)
            if leg:
                legs.append(leg)
    if not legs:
        return {"ok": False, "error": "At least one valid leg required."}

    try:
        spot = float(payload.get("spot"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "spot is required."}
    if not math.isfinite(spot) or spot <= 0:
        return {"ok": False, "error": "Invalid spot."}

    params = {
        "days": int(payload.get("days") or 10),
        "shock_pct": float(payload.get("shock_pct") or 2),
        "path_bias": str(payload.get("path_bias") or "flat"),
        "strike_step": payload.get("strike_step"),
        "path_source": "historical"
        if payload.get("use_historical") or payload.get("closes")
        else "synthetic",
        "use_marks": bool(payload.get("use_marks")),
        "iv_pct": payload.get("iv_pct"),
        "entry_dte": payload.get("entry_dte"),
    }
    closes_raw = payload.get("closes") if isinstance(payload.get("closes"), list) else None
    closes: list[float] = []
    if closes_raw:
        for c in closes_raw[:400]:
            try:
                v = float(c)
            except (TypeError, ValueError):
                continue
            if v > 0:
                closes.append(v)

    if payload.get("use_marks"):
        try:
            iv_pct = float(payload.get("iv_pct") or 15)
        except (TypeError, ValueError):
            iv_pct = 15.0
        try:
            entry_dte = (
                float(payload["entry_dte"])
                if payload.get("entry_dte") is not None
                else None
            )
        except (TypeError, ValueError):
            entry_dte = None
        result = run_bs_mark_backtest(
            legs=legs,
            spot=spot,
            days=params["days"],
            shock_pct=params["shock_pct"],
            path_bias=params["path_bias"],
            iv_pct=iv_pct,
            entry_dte=entry_dte,
            closes=closes if len(closes) >= MIN_DAYS else None,
        )
    elif closes and len(closes) >= MIN_DAYS:
        result = run_historical_close_backtest(
            legs=legs, closes=closes, shock_pct=params["shock_pct"]
        )
    else:
        result = run_model_backtest(
            legs=legs,
            spot=spot,
            days=params["days"],
            shock_pct=params["shock_pct"],
            path_bias=params["path_bias"],
        )
    if result is None:
        return {"ok": False, "error": "Model backtest failed."}

    items = await load_backtests(tenant_id)
    if len(items) >= MAX_BACKTESTS:
        return {"ok": False, "error": f"Maximum {MAX_BACKTESTS} saved backtests reached."}

    name = str(payload.get("name") or "").strip() or f"Model · {params['days']}d"
    now = _now_ts()
    row = {
        "id": f"bt-{uuid.uuid4().hex[:12]}",
        "owner_id": str(owner_id) if owner_id else None,
        "name": name[:120],
        "fidelity": result.get("fidelity") or "model",
        "underlying_symbol": str(payload.get("underlying_symbol") or "").strip() or None,
        "underlying_label": str(payload.get("underlying_label") or "").strip() or None,
        "spot": spot,
        "params": params,
        "legs": legs,
        "result": result,
        "created_at": now,
        "updated_at": now,
        "source": "lab",
    }
    items.insert(0, row)
    await save_backtests(tenant_id, items)
    return {"ok": True, "backtest": row}


async def summarize_backtests(
    tenant_id: str,
    *,
    ids: list[str] | None = None,
    limit: int = 5,
    owner_id: str | None = None,
) -> dict[str, Any]:
    items = visible_rows(await load_backtests(tenant_id), owner_id)
    if ids:
        wanted = set(ids)
        selected = [row for row in items if row.get("id") in wanted]
    else:
        selected = items[: max(1, min(10, int(limit or 5)))]
    return portfolio_summary(selected)
