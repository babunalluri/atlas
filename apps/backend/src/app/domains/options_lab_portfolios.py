"""Options Lab draft portfolios — tenant session store + mark-to-market."""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

from app.domains.desk_snapshot import normalize_positions, unwrap_records
from app.domains.signal_engine import _find_quote_row, _pick_float
from app.domains.signal_engine_cache import get_session_value, set_session_value

PORTFOLIOS_FIELD = "options_lab:portfolios"
MAX_PORTFOLIOS = 24
OPTION_SYMBOL_MONTHLY_RE = re.compile(
    r"^(?P<root>[A-Z]+)(?P<expiry>\d{2}[A-Z]{3})(?P<strike>\d+)(?P<side>CE|PE)$",
    re.IGNORECASE,
)
OPTION_SYMBOL_WEEKLY_ALPHA_RE = re.compile(
    r"^(?P<root>[A-Z]+)(?P<expiry>\d{2}[A-Z]\d{2})(?P<strike>\d+)(?P<side>CE|PE)$",
    re.IGNORECASE,
)
SENSEX_ROOTS = {"SENSEX"}
INDEX_OPTION_ROOTS = ("MIDCPNIFTY", "BANKNIFTY", "FINNIFTY", "NIFTY", "SENSEX")


def _now_ts() -> int:
    return int(time.time())


def _leg_sign(side: str) -> int:
    return 1 if str(side).lower() == "buy" else -1


def leg_mtm(*, side: str, entry_premium: float, current_premium: float, qty: float) -> float:
    return round(_leg_sign(side) * (current_premium - entry_premium) * qty, 2)


def _parsed_option_match(symbol: str, raw: str, match: re.Match[str]) -> dict[str, Any]:
    side = match.group("side").upper()
    try:
        strike = int(match.group("strike"))
    except ValueError:
        raise ValueError("invalid strike")
    return {
        "symbol": symbol.strip(),
        "root": match.group("root").upper(),
        "expiry": match.group("expiry").upper(),
        "strike": strike,
        "type": side,
    }


def _parse_weekly_digits_option(symbol: str, raw: str) -> dict[str, Any] | None:
    """Weekly index options: NIFTY2580724500CE (expiry code + strike, no month letters)."""
    if not raw.endswith(("CE", "PE")):
        return None
    side = raw[-2:].upper()
    body = raw[:-2]
    for root in INDEX_OPTION_ROOTS:
        if not body.startswith(root):
            continue
        tail = body[len(root) :]
        if not tail.isdigit():
            continue
        for strike_len in (5, 4, 6):
            if len(tail) <= strike_len + 4:
                continue
            strike_part = tail[-strike_len:]
            expiry_part = tail[:-strike_len]
            if expiry_part.isdigit() and len(expiry_part) >= 5:
                return {
                    "symbol": symbol.strip(),
                    "root": root,
                    "expiry": expiry_part,
                    "strike": int(strike_part),
                    "type": side,
                }
    return None


def parse_option_symbol(symbol: str) -> dict[str, Any] | None:
    raw = (symbol or "").strip().upper()
    if ":" in raw:
        _, raw = raw.split(":", 1)
    if not raw.endswith(("CE", "PE")):
        return None
    for pattern in (OPTION_SYMBOL_MONTHLY_RE, OPTION_SYMBOL_WEEKLY_ALPHA_RE):
        match = pattern.match(raw)
        if not match:
            continue
        try:
            return _parsed_option_match(symbol, raw, match)
        except ValueError:
            continue
    return _parse_weekly_digits_option(symbol, raw)


def option_exchange(root: str) -> str:
    return "BFO" if root.upper() in SENSEX_ROOTS else "NFO"


def canonical_broker_option_symbol(symbol: str) -> str:
    raw = (symbol or "").strip().upper()
    if not raw:
        return ""
    if ":" in raw:
        return raw.replace(" ", "")
    parsed = parse_option_symbol(raw)
    if parsed is None:
        if raw.endswith(("CE", "PE")):
            for root in INDEX_OPTION_ROOTS:
                if raw.startswith(root):
                    return f"{option_exchange(root)}:{raw}"
        return raw
    return f"{option_exchange(parsed['root'])}:{raw}"


def infer_fut_symbol_from_legs(legs: list[dict[str, Any]]) -> str:
    for leg in legs:
        sym = str(leg.get("symbol") or "").strip()
        if not sym:
            continue
        parsed = parse_option_symbol(sym)
        if parsed is None:
            continue
        # Weekly option expiries do not map to a real futures contract symbol.
        if not re.fullmatch(r"\d{2}[A-Z]{3}", str(parsed["expiry"])):
            continue
        exchange = option_exchange(parsed["root"])
        return f"{exchange}:{parsed['root']}{parsed['expiry']}FUT"
    return ""


def normalize_portfolio_leg(raw: dict[str, Any], *, index: int) -> dict[str, Any] | None:
    side = str(raw.get("side") or "").lower()
    opt_type = str(raw.get("type") or raw.get("option_type") or "").upper()
    if side not in {"buy", "sell"} or opt_type not in {"CE", "PE"}:
        return None
    try:
        strike = int(raw.get("strike"))
        qty = float(raw.get("qty") if raw.get("qty") is not None else 1)
        entry = float(raw.get("entry_premium"))
    except (TypeError, ValueError):
        return None
    if strike <= 0 or qty == 0 or entry < 0:
        return None
    leg_id = str(raw.get("id") or f"leg-{index}")
    symbol = canonical_broker_option_symbol(str(raw.get("symbol") or ""))
    return {
        "id": leg_id,
        "side": side,
        "type": opt_type,
        "strike": strike,
        "qty": qty,
        "entry_premium": round(entry, 4),
        "symbol": symbol,
    }


def normalize_portfolio(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    legs_in = raw.get("legs")
    if not isinstance(legs_in, list) or not legs_in:
        return None
    legs: list[dict[str, Any]] = []
    for idx, item in enumerate(legs_in):
        if not isinstance(item, dict):
            continue
        leg = normalize_portfolio_leg(item, index=idx)
        if leg:
            legs.append(leg)
    if not legs:
        return None
    now = _now_ts()
    portfolio_id = str(raw.get("id") or uuid.uuid4().hex[:12])
    source = str(raw.get("source") or "manual")
    if source not in {"manual", "builder", "kite_import"}:
        source = "manual"
    return {
        "id": portfolio_id,
        "name": name[:120],
        "underlying_symbol": str(raw.get("underlying_symbol") or "").strip(),
        "underlying_label": str(raw.get("underlying_label") or "").strip(),
        "fut_symbol": str(raw.get("fut_symbol") or "").strip(),
        "strike_step": int(raw.get("strike_step") or 50),
        "source": source,
        "created_at": int(raw.get("created_at") or now),
        "updated_at": int(raw.get("updated_at") or now),
        "legs": legs,
    }


async def load_portfolios(tenant_id: str) -> list[dict[str, Any]]:
    stored = await get_session_value(tenant_id, PORTFOLIOS_FIELD)
    if not isinstance(stored, dict):
        return []
    rows = stored.get("items")
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for item in rows:
        if isinstance(item, dict):
            out.append(item)
    return out


async def save_portfolios(tenant_id: str, items: list[dict[str, Any]]) -> None:
    await set_session_value(
        tenant_id,
        PORTFOLIOS_FIELD,
        {"items": items[:MAX_PORTFOLIOS], "updated_at": _now_ts()},
    )


async def list_portfolios(tenant_id: str) -> dict[str, Any]:
    items = await load_portfolios(tenant_id)
    return {"ok": True, "portfolios": items, "count": len(items)}


async def get_portfolio(tenant_id: str, portfolio_id: str) -> dict[str, Any] | None:
    for item in await load_portfolios(tenant_id):
        if item.get("id") == portfolio_id:
            return item
    return None


async def create_portfolio(tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_portfolio(payload)
    if normalized is None:
        return {"ok": False, "error": "Invalid portfolio — name and at least one leg required."}
    items = await load_portfolios(tenant_id)
    if len(items) >= MAX_PORTFOLIOS:
        return {"ok": False, "error": f"Maximum {MAX_PORTFOLIOS} draft portfolios reached."}
    items.insert(0, normalized)
    await save_portfolios(tenant_id, items)
    return {"ok": True, "portfolio": normalized}


async def update_portfolio(
    tenant_id: str,
    portfolio_id: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    items = await load_portfolios(tenant_id)
    idx = next((i for i, row in enumerate(items) if row.get("id") == portfolio_id), None)
    if idx is None:
        return {"ok": False, "error": "Portfolio not found."}
    current = dict(items[idx])
    if "name" in patch and patch["name"] is not None:
        name = str(patch["name"]).strip()
        if name:
            current["name"] = name[:120]
    if "legs" in patch and isinstance(patch["legs"], list):
        legs: list[dict[str, Any]] = []
        for leg_idx, item in enumerate(patch["legs"]):
            if isinstance(item, dict):
                leg = normalize_portfolio_leg(item, index=leg_idx)
                if leg:
                    legs.append(leg)
        if not legs:
            return {"ok": False, "error": "At least one valid leg required."}
        current["legs"] = legs
    for key in ("underlying_symbol", "underlying_label", "fut_symbol"):
        if key in patch and patch[key] is not None:
            current[key] = str(patch[key]).strip()
    if "strike_step" in patch and patch["strike_step"] is not None:
        try:
            current["strike_step"] = max(1, int(patch["strike_step"]))
        except (TypeError, ValueError):
            pass
    current["updated_at"] = _now_ts()
    items[idx] = current
    await save_portfolios(tenant_id, items)
    return {"ok": True, "portfolio": current}


async def delete_portfolio(tenant_id: str, portfolio_id: str) -> dict[str, Any]:
    items = await load_portfolios(tenant_id)
    next_items = [row for row in items if row.get("id") != portfolio_id]
    if len(next_items) == len(items):
        return {"ok": False, "error": "Portfolio not found."}
    await save_portfolios(tenant_id, next_items)
    return {"ok": True}


def _option_symbols_for_legs(
    fut_symbol: str,
    legs: list[dict[str, Any]],
) -> list[str]:
    from app.domains.signal_engine_chain import _derive_option_symbols

    ce_strikes = [int(leg["strike"]) for leg in legs if leg.get("type") == "CE"]
    pe_strikes = [int(leg["strike"]) for leg in legs if leg.get("type") == "PE"]
    symbols: list[str] = []
    if ce_strikes:
        symbols.extend(_derive_option_symbols(fut_symbol, ce_strikes, "CE"))
    if pe_strikes:
        symbols.extend(_derive_option_symbols(fut_symbol, pe_strikes, "PE"))
    # Preserve order while deduping
    seen: set[str] = set()
    ordered: list[str] = []
    for sym in symbols:
        if sym and sym not in seen:
            seen.add(sym)
            ordered.append(sym)
    return ordered


def mark_portfolio_legs(
    portfolio: dict[str, Any],
    *,
    quotes: dict[str, Any],
    mock: bool = False,
) -> dict[str, Any]:
    legs_out: list[dict[str, Any]] = []
    total_entry = 0.0
    total_mtm = 0.0
    total_current = 0.0
    missing_quotes = 0

    for leg in portfolio.get("legs") or []:
        if not isinstance(leg, dict):
            continue
        side = str(leg.get("side") or "buy")
        opt_type = str(leg.get("type") or "CE").upper()
        strike = int(leg.get("strike") or 0)
        qty = float(leg.get("qty") or 1)
        entry = float(leg.get("entry_premium") or 0)
        symbol = canonical_broker_option_symbol(str(leg.get("symbol") or ""))

        current: float | None = None
        if symbol:
            row = _find_quote_row(quotes, symbol)
            current = _pick_float(row or {}, "last_price", "ltp", "last")
        if current is None and portfolio.get("fut_symbol"):
            from app.domains.signal_engine_chain import _derive_option_symbols

            derived = _derive_option_symbols(str(portfolio["fut_symbol"]), [strike], opt_type)
            if derived:
                row = _find_quote_row(quotes, derived[0])
                current = _pick_float(row or {}, "last_price", "ltp", "last")
                if not symbol:
                    symbol = derived[0]

        entry_cash = -_leg_sign(side) * entry * qty
        total_entry += entry_cash

        if current is None:
            missing_quotes += 1
            mtm = 0.0
            current_value = None
        else:
            mtm = leg_mtm(
                side=side,
                entry_premium=entry,
                current_premium=current,
                qty=qty,
            )
            current_value = round(current, 4)
            total_mtm += mtm
            total_current += _leg_sign(side) * current * qty

        legs_out.append(
            {
                **leg,
                "symbol": symbol,
                "current_premium": current_value,
                "mtm": mtm if current_value is not None else None,
                "entry_cash": round(entry_cash, 2),
            }
        )

    return {
        "portfolio_id": portfolio.get("id"),
        "name": portfolio.get("name"),
        "underlying_symbol": portfolio.get("underlying_symbol"),
        "underlying_label": portfolio.get("underlying_label"),
        "fut_symbol": portfolio.get("fut_symbol"),
        "mock": mock,
        "marked_at": _now_ts(),
        "legs": legs_out,
        "summary": {
            "net_entry_cash": round(total_entry, 2),
            "net_current_value": round(total_current, 2) if missing_quotes == 0 else None,
            "total_mtm": round(total_mtm, 2),
            "missing_quotes": missing_quotes,
        },
    }


def positions_to_portfolio_legs(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    legs: list[dict[str, Any]] = []
    for idx, row in enumerate(positions):
        symbol = str(row.get("symbol") or "").strip()
        parsed = parse_option_symbol(symbol)
        if not parsed:
            continue
        qty_raw = row.get("qty")
        try:
            qty = float(qty_raw)
        except (TypeError, ValueError):
            continue
        if qty == 0:
            continue
        side = "buy" if qty > 0 else "sell"
        qty_abs = abs(qty)
        avg = row.get("avg")
        try:
            entry = float(avg)
        except (TypeError, ValueError):
            entry = 0.0
        ltp = row.get("ltp")
        if entry <= 0 and ltp is not None:
            try:
                entry = float(ltp)
            except (TypeError, ValueError):
                entry = 0.0
        legs.append(
            {
                "id": f"kite-{idx}",
                "side": side,
                "type": parsed["type"],
                "strike": parsed["strike"],
                "qty": qty_abs,
                "entry_premium": round(max(0.0, entry), 4),
                "symbol": canonical_broker_option_symbol(symbol),
            }
        )
    return legs


def kite_positions_payload(raw: Any) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    if isinstance(raw, dict) and raw.get("ok") is False:
        return [], [str(raw.get("error") or "Kite positions request failed")]
    rows = normalize_positions(raw)
    if not rows and isinstance(raw, dict):
        net = raw.get("net")
        if isinstance(net, list):
            rows = normalize_positions(net)
    if not rows and raw is not None:
        extra = unwrap_records(raw)
        if extra:
            rows = normalize_positions(extra)
    legs = positions_to_portfolio_legs(rows)
    if not legs:
        warnings.append("No open F&O option positions found in broker book.")
    return legs, warnings
