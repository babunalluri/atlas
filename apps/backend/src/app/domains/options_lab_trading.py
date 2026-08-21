"""Options Lab broker margins + multi-leg place-order helpers."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.factory import AgentFactoryService, McpToolSkipped
from app.db.repositories import TeamRepository, ToolDefinitionRepository
from app.domains.desk_snapshot import DESK_TEAM_SLUGS, invoke_tool
from app.domains.options_lab_portfolios import (
    canonical_broker_option_symbol,
    option_exchange,
    parse_option_symbol,
)
from app.domains.signal_engine import SIGNAL_TEAM_SLUG

MARGIN_TOOL_NAMES = (
    "get_order_margins",
    "get_required_margin",
    "get_user_margins",
    "get_user_margin",
    "get_funds",
    "get_margins",
)
ORDER_TOOL_NAMES = ("place_order", "place_paper_order")

LOT_SIZE_BY_ROOT = {
    "NIFTYNXT50": 25,
    "NIFTY": 75,
    "BANKNIFTY": 15,
    "FINNIFTY": 25,
    "MIDCPNIFTY": 50,
    "SENSEX": 10,
}


def estimate_lot_size(underlying: str | None = None, root: str | None = None) -> int:
    text = f"{underlying or ''} {root or ''}".upper()
    # Longer roots first so BANKNIFTY / MIDCPNIFTY are not matched as NIFTY.
    for key, lot in sorted(LOT_SIZE_BY_ROOT.items(), key=lambda item: -len(item[0])):
        if key in text:
            return lot
    return 75


def _pick_number(payload: Any, *keys: str) -> float | None:
    if isinstance(payload, (int, float)) and not isinstance(payload, bool):
        return float(payload)
    if not isinstance(payload, dict):
        return None
    for key in keys:
        if key in payload and payload[key] is not None:
            try:
                return float(payload[key])
            except (TypeError, ValueError):
                continue
        nested = payload.get(key)
        if isinstance(nested, dict):
            found = _pick_number(
                nested,
                "available",
                "live_balance",
                "cash",
                "net",
                "total",
                "option_premium",
                "span",
                "exposure",
            )
            if found is not None:
                return found
    for value in payload.values():
        if isinstance(value, dict):
            found = _pick_number(value, *keys)
            if found is not None:
                return found
    return None


def _margin_total_from_order_payload(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        total = 0.0
        any_hit = False
        for row in raw:
            val = _pick_number(row, "total", "total_margin", "final_margin", "margin")
            if val is not None:
                total += val
                any_hit = True
        return total if any_hit else None
    if isinstance(raw, dict):
        data = raw.get("data", raw)
        if isinstance(data, list):
            return _margin_total_from_order_payload(data)
        return _pick_number(data, "total", "total_margin", "final_margin", "margin", "required")
    return None


_REJECT_STATUSES = frozenset(
    {
        "REJECTED",
        "REJECT",
        "CANCELLED",
        "CANCELED",
        "FAILED",
        "ERROR",
        "INVALID",
        "DISALLOWED",
    }
)


def _order_submission_result(raw: Any) -> tuple[bool, Any, str | None]:
    """Return (accepted, order_id, error). Non-throwing broker rejects are failures."""
    if raw is None:
        return False, None, "empty broker response"
    if isinstance(raw, (str, int)):
        text = str(raw).strip()
        return (True, text, None) if text else (False, None, "empty order id")
    if not isinstance(raw, dict):
        return False, None, "unrecognized broker response"
    if raw.get("ok") is False or raw.get("success") is False:
        return False, None, str(raw.get("error") or raw.get("message") or "ok=false")
    status = str(
        raw.get("status")
        or raw.get("order_status")
        or raw.get("state")
        or ""
    ).upper()
    if status in _REJECT_STATUSES:
        return False, None, status or "rejected"
    top = status.lower()
    if top in {"error", "failure", "failed"}:
        return False, None, str(raw.get("message") or raw.get("error") or top)

    nested = raw.get("data")
    if isinstance(nested, (str, int)) and str(nested).strip():
        return True, nested, None
    if isinstance(nested, dict):
        nested_status = str(
            nested.get("status") or nested.get("order_status") or nested.get("state") or ""
        ).upper()
        if nested_status in _REJECT_STATUSES:
            return False, None, nested_status or "rejected"
        order_id = nested.get("order_id") or nested.get("groww_order_id") or nested.get("id")
        if order_id is not None and str(order_id).strip():
            return True, order_id, None

    order_id = raw.get("order_id") or raw.get("groww_order_id") or raw.get("id")
    if isinstance(order_id, dict):
        order_id = order_id.get("order_id")
    if order_id is not None and str(order_id).strip():
        return True, order_id, None

    # Explicit success without an id is still ambiguous — treat as failure so UI
    # cannot claim a fill that cannot be tracked.
    if top in {"success", "ok", "complete", "completed", "open", "pending", "trigger pending"}:
        return False, None, "broker success without order_id"
    return False, None, "no order_id in broker response"


def _leg_qty_lots(leg: dict[str, Any]) -> float | None:
    try:
        qty_lots = float(leg.get("qty") if leg.get("qty") is not None else 1)
    except (TypeError, ValueError):
        return None
    if qty_lots <= 0:
        return None
    return qty_lots


def _available_from_margins_payload(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        data = raw.get("data", raw)
        # Kite: equity.available.live_balance / net
        for segment in ("equity", "commodity"):
            seg = data.get(segment) if isinstance(data, dict) else None
            if isinstance(seg, dict):
                avail = seg.get("available") if isinstance(seg.get("available"), dict) else seg
                val = _pick_number(
                    avail,
                    "live_balance",
                    "cash",
                    "net",
                    "available_balance",
                    "available_margin",
                )
                if val is not None:
                    return val
        return _pick_number(
            data,
            "live_balance",
            "cash",
            "net",
            "available_balance",
            "available_margin",
            "available",
        )
    return None


def _split_exchange_symbol(symbol: str) -> tuple[str, str]:
    raw = canonical_broker_option_symbol(symbol)
    if ":" in raw:
        exchange, tradingsymbol = raw.split(":", 1)
        return exchange.upper(), tradingsymbol.strip().upper()
    parsed = parse_option_symbol(raw)
    if parsed:
        return option_exchange(parsed["root"]), raw.upper()
    return "NFO", raw.upper()


class OptionsLabTradingService:
    def __init__(self, session: AsyncSession, context: Any) -> None:
        self.session = session
        self.context = context
        self.teams = TeamRepository(session)
        self.tools = ToolDefinitionRepository(session)
        self.factory = AgentFactoryService(session, context)

    async def _iter_team_bindings(self, slugs: tuple[str, ...]):
        for slug in slugs:
            config = await self.teams.get_config_by_slug(slug)
            if config is None or config.published_version_id is None:
                continue
            version = await self.teams.get_version(config.published_version_id)
            if version is None:
                continue
            bindings = await self.teams.bindings(version.id)
            for binding in bindings:
                yield config, binding

    async def _find_tool(self, names: tuple[str, ...], *, team_slugs: tuple[str, ...]):
        wanted = set(names)
        async for team, binding in self._iter_team_bindings(team_slugs):
            if binding.tool_definition_id is None:
                continue
            try:
                built = await self.factory._build_tool(binding)
            except McpToolSkipped:
                continue
            except Exception:
                continue
            callables = built if isinstance(built, list) else [built]
            for fn in callables:
                name = getattr(fn, "__name__", "")
                if name in wanted:
                    return fn, name, team.slug
        return None, None, None

    async def strategy_margins(
        self,
        *,
        legs: list[dict[str, Any]],
        lot_size: int,
        product: str = "NRML",
        mock: bool = False,
        heuristic: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not legs:
            return {"ok": False, "error": "No legs."}

        available = None
        margin_needed = None
        source = "heuristic"
        warnings: list[str] = []

        if mock:
            source = "mock_heuristic"
        else:
            avail_fn, avail_name, _ = await self._find_tool(
                ("get_user_margins", "get_user_margin", "get_funds", "get_margins"),
                team_slugs=(SIGNAL_TEAM_SLUG, *DESK_TEAM_SLUGS),
            )
            if avail_fn is not None:
                try:
                    raw = await invoke_tool(avail_fn, {})
                    available = _available_from_margins_payload(raw)
                    if available is None and avail_name:
                        warnings.append(f"{avail_name} returned no usable available cash.")
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"Available margins failed: {exc}")

            order_fn, order_name, _ = await self._find_tool(
                ("get_order_margins", "get_required_margin"),
                team_slugs=(SIGNAL_TEAM_SLUG, *DESK_TEAM_SLUGS),
            )
            if order_fn is not None:
                totals: list[float] = []
                priced_legs = 0
                skipped_legs = 0
                for leg in legs:
                    symbol = str(leg.get("symbol") or "").strip()
                    if not symbol:
                        skipped_legs += 1
                        warnings.append("Margin skipped leg without symbol.")
                        continue
                    exchange, tradingsymbol = _split_exchange_symbol(symbol)
                    side = str(leg.get("side") or "buy").lower()
                    txn = "BUY" if side == "buy" else "SELL"
                    qty_lots = _leg_qty_lots(leg)
                    if qty_lots is None:
                        skipped_legs += 1
                        warnings.append(f"Margin skipped {tradingsymbol}: qty must be > 0.")
                        continue
                    quantity = int(round(qty_lots * lot_size))
                    if quantity < 1:
                        skipped_legs += 1
                        warnings.append(
                            f"Margin skipped {tradingsymbol}: quantity rounds to 0."
                        )
                        continue
                    try:
                        premium = float(leg.get("entry_premium") or leg.get("premium") or 0)
                    except (TypeError, ValueError):
                        premium = 0.0
                    kwargs = {
                        "exchange": exchange,
                        "tradingsymbol": tradingsymbol,
                        "transaction_type": txn,
                        "variety": "regular",
                        "product": product.upper(),
                        "order_type": "LIMIT" if premium > 0 else "MARKET",
                        "quantity": quantity,
                        "price": premium if premium > 0 else 0,
                    }
                    # Groww-style required margin uses a different shape — best effort.
                    if order_name == "get_required_margin":
                        kwargs = {
                            "exchange": exchange,
                            "trading_symbol": tradingsymbol,
                            "transaction_type": txn,
                            "quantity": quantity,
                            "product": product.upper(),
                            "segment": "FNO",
                            "order_type": "LIMIT" if premium > 0 else "MARKET",
                            "price": premium if premium > 0 else 0,
                        }
                    try:
                        raw = await invoke_tool(order_fn, kwargs)
                        total = _margin_total_from_order_payload(raw)
                        if total is not None:
                            totals.append(total)
                            priced_legs += 1
                        else:
                            skipped_legs += 1
                            warnings.append(
                                f"Order margin for {tradingsymbol} returned no total."
                            )
                    except Exception as exc:  # noqa: BLE001
                        skipped_legs += 1
                        warnings.append(f"Order margin for {tradingsymbol} failed: {exc}")
                incomplete = skipped_legs > 0 or priced_legs < len(legs)
                if totals and not incomplete:
                    margin_needed = round(sum(totals), 2)
                    source = order_name or "broker_margins"
                elif totals and incomplete:
                    # Partial broker sum is not trustworthy as a live margin figure.
                    warnings.append(
                        f"Broker margins incomplete ({priced_legs}/{len(legs)} legs) — "
                        "using heuristic."
                    )
                    margin_needed = None
                    source = "heuristic"
                elif not totals:
                    margin_needed = None

            if margin_needed is None and not mock:
                if not any("incomplete" in w for w in warnings):
                    warnings.append(
                        "Broker order-margin tool unavailable or failed — using heuristic."
                    )

        if margin_needed is None and heuristic:
            margin_needed = (
                heuristic.get("marginNeeded")
                or heuristic.get("margin_needed")
                or heuristic.get("fundsNeeded")
                or heuristic.get("funds_needed")
            )
            if source == "heuristic" or mock:
                source = "mock_heuristic" if mock else "heuristic"

        return {
            "ok": True,
            "source": source,
            "funds_needed": margin_needed,
            "margin_needed": margin_needed,
            "margin_available": available,
            "lot_size": lot_size,
            "product": product.upper(),
            # Per-leg sum overstates portfolio SPAN for multi-leg hedges.
            "basket": False,
            "estimated": source in {"heuristic", "mock_heuristic"},
            "warnings": warnings
            + (
                ["Broker margins are summed per leg — not exchange basket SPAN."]
                if source not in {"heuristic", "mock_heuristic"} and margin_needed is not None
                else []
            ),
        }

    async def place_strategy_orders(
        self,
        *,
        legs: list[dict[str, Any]],
        lot_size: int,
        product: str = "NRML",
        order_type: str = "LIMIT",
        confirm: bool = False,
        live: bool = False,
        mock: bool = False,
        tag: str = "atlas-ol",
    ) -> dict[str, Any]:
        if not confirm:
            return {"ok": False, "error": "Set confirm=true to place orders."}
        if not legs:
            return {"ok": False, "error": "No legs."}

        missing_symbols = [
            idx
            for idx, leg in enumerate(legs)
            if not str(leg.get("symbol") or "").strip()
        ]
        if missing_symbols:
            return {
                "ok": False,
                "error": f"Missing option symbols on legs {missing_symbols} — load chain strikes first.",
            }

        if mock:
            return {
                "ok": True,
                "mock": True,
                "orders": [
                    {
                        "symbol": leg.get("symbol"),
                        "side": leg.get("side"),
                        "status": "simulated",
                        "order_id": f"mock-{idx}",
                    }
                    for idx, leg in enumerate(legs)
                ],
                "warnings": ["Mock mode — no broker orders sent."],
            }

        # Prefer paper path; live place_order requires explicit live=true.
        team_order = ("paper-trading", "live-trading", SIGNAL_TEAM_SLUG)
        place_fn, place_name, team_slug = await self._find_tool(
            ("place_paper_order",),
            team_slugs=team_order,
        )
        if place_fn is None:
            place_fn, place_name, team_slug = await self._find_tool(
                ("place_order",),
                team_slugs=team_order,
            )
        if place_fn is None:
            return {
                "ok": False,
                "error": (
                    "No place_order / place_paper_order tool bound on Paper, Live, "
                    "or Signals ops teams."
                ),
            }
        if place_name == "place_order" and not live:
            return {
                "ok": False,
                "error": (
                    "Live place_order requires live=true (paper_order preferred when bound). "
                    "Re-confirm in the UI to send live broker orders."
                ),
                "tool": place_name,
                "team_slug": team_slug,
            }

        # Buys first so hedges land before short legs (iron condor / credit spreads).
        ordered = sorted(
            enumerate(legs),
            key=lambda item: (
                0 if str(item[1].get("side") or "buy").lower() == "buy" else 1,
                item[0],
            ),
        )

        results: list[dict[str, Any]] = []
        errors: list[str] = []
        buy_failed = False
        for idx, leg in ordered:
            side = str(leg.get("side") or "buy").lower()
            # If any buy/hedge failed, skip remaining sells to avoid naked shorts.
            if buy_failed and side != "buy":
                errors.append(f"Leg {idx}: skipped sell after buy failure")
                results.append(
                    {
                        "leg_index": idx,
                        "symbol": leg.get("symbol"),
                        "side": side,
                        "status": "skipped",
                        "error": "skipped sell after buy failure",
                    }
                )
                continue
            symbol = str(leg.get("symbol") or "").strip()
            if not symbol:
                errors.append(f"Leg {idx}: missing symbol")
                results.append(
                    {
                        "leg_index": idx,
                        "symbol": None,
                        "side": leg.get("side"),
                        "status": "error",
                        "error": "missing symbol",
                    }
                )
                if side == "buy":
                    buy_failed = True
                continue
            exchange, tradingsymbol = _split_exchange_symbol(symbol)
            txn = "BUY" if side == "buy" else "SELL"
            qty_lots = _leg_qty_lots(leg)
            if qty_lots is None:
                errors.append(f"Leg {idx}: qty must be > 0")
                results.append(
                    {
                        "leg_index": idx,
                        "symbol": f"{exchange}:{tradingsymbol}",
                        "side": side,
                        "status": "error",
                        "error": "qty must be > 0",
                    }
                )
                if side == "buy":
                    buy_failed = True
                continue
            quantity = int(round(qty_lots * lot_size))
            if quantity < 1:
                errors.append(f"Leg {idx}: quantity rounds to 0 (qty={qty_lots}, lot={lot_size})")
                results.append(
                    {
                        "leg_index": idx,
                        "symbol": f"{exchange}:{tradingsymbol}",
                        "side": side,
                        "status": "error",
                        "error": "quantity rounds to 0",
                    }
                )
                if side == "buy":
                    buy_failed = True
                continue
            try:
                premium = float(leg.get("entry_premium") or leg.get("premium") or 0)
            except (TypeError, ValueError):
                premium = 0.0
            ot = order_type.upper()
            if ot == "LIMIT" and not (premium > 0):
                errors.append(f"{tradingsymbol}: LIMIT requires premium > 0")
                results.append(
                    {
                        "leg_index": idx,
                        "symbol": f"{exchange}:{tradingsymbol}",
                        "side": side,
                        "quantity": quantity,
                        "status": "error",
                        "error": "LIMIT requires premium > 0",
                    }
                )
                if side == "buy":
                    buy_failed = True
                continue
            kwargs: dict[str, Any] = {
                "tradingsymbol": tradingsymbol,
                "exchange": exchange,
                "transaction_type": txn,
                "quantity": quantity,
                "order_type": ot,
                "product": product.upper(),
                "variety": "regular",
                "tag": f"{tag}{idx}"[:20],
            }
            if ot == "LIMIT":
                kwargs["price"] = premium
            try:
                raw = await invoke_tool(place_fn, kwargs)
                accepted, order_id, reject_reason = _order_submission_result(raw)
                if not accepted:
                    errors.append(f"{tradingsymbol}: {reject_reason or 'rejected'}")
                    results.append(
                        {
                            "leg_index": idx,
                            "symbol": f"{exchange}:{tradingsymbol}",
                            "side": side,
                            "quantity": quantity,
                            "status": "rejected",
                            "error": reject_reason or "rejected",
                            "raw": raw,
                        }
                    )
                    if side == "buy":
                        buy_failed = True
                    continue
                results.append(
                    {
                        "leg_index": idx,
                        "symbol": f"{exchange}:{tradingsymbol}",
                        "side": side,
                        "quantity": quantity,
                        "status": "submitted",
                        "order_id": order_id,
                        "raw": raw,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{tradingsymbol}: {exc}")
                results.append(
                    {
                        "leg_index": idx,
                        "symbol": f"{exchange}:{tradingsymbol}",
                        "side": side,
                        "quantity": quantity,
                        "status": "error",
                        "error": str(exc),
                    }
                )
                if side == "buy":
                    buy_failed = True

        submitted = [r for r in results if r.get("status") == "submitted"]
        failed = [r for r in results if r.get("status") != "submitted"]
        partial = bool(submitted) and bool(failed)
        return {
            "ok": len(failed) == 0 and len(submitted) > 0,
            "partial": partial,
            "tool": place_name,
            "team_slug": team_slug,
            "orders": results,
            "submitted_count": len(submitted),
            "failed_count": len(failed),
            "errors": errors,
            "warnings": (
                [
                    "PARTIAL FILL — some legs submitted. Check positions before Buy again "
                    "(re-sending all legs can double the book)."
                ]
                if partial
                else []
            ),
        }
