"""Options Lab broker margins + multi-leg place-order helpers."""

from __future__ import annotations

import asyncio
import json
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
GTT_TOOL_NAMES = ("place_gtt",)
GTT_LIST_TOOL_NAMES = ("list_gtts",)
GTT_DELETE_TOOL_NAMES = ("delete_gtt",)

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


def _basket_margin_totals(raw: Any) -> tuple[float | None, float | None]:
    """Return (initial_total, final_total) from Kite basket margins payload."""
    if not isinstance(raw, dict):
        return None, None
    data = raw.get("data", raw)
    if not isinstance(data, dict):
        return None, None
    initial = data.get("initial")
    final = data.get("final")
    init_total = (
        _pick_number(initial, "total") if isinstance(initial, dict) else None
    )
    final_total = _pick_number(final, "total") if isinstance(final, dict) else None
    return init_total, final_total


def _leg_to_margin_order(
    leg: dict[str, Any],
    *,
    lot_size: int,
    product: str,
) -> dict[str, Any] | None:
    """Build a Kite order-margin / basket-margin item, or None if invalid."""
    symbol = str(leg.get("symbol") or "").strip()
    if not symbol:
        return None
    exchange, tradingsymbol = _split_exchange_symbol(symbol)
    side = str(leg.get("side") or "buy").lower()
    txn = "BUY" if side == "buy" else "SELL"
    qty_lots = _leg_qty_lots(leg)
    if qty_lots is None:
        return None
    quantity = int(round(qty_lots * lot_size))
    if quantity < 1:
        return None
    try:
        premium = float(leg.get("entry_premium") or leg.get("premium") or 0)
    except (TypeError, ValueError):
        premium = 0.0
    return {
        "exchange": exchange,
        "tradingsymbol": tradingsymbol,
        "transaction_type": txn,
        "variety": "regular",
        "product": product.upper(),
        "order_type": "LIMIT" if premium > 0 else "MARKET",
        "quantity": quantity,
        "price": premium if premium > 0 else 0,
        "trigger_price": 0,
    }


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
    snap = margins_snapshot_from_payload(raw)
    return snap.get("available_cash")


def margins_snapshot_from_payload(raw: Any) -> dict[str, Any]:
    """Parse Kite-style margins into available + used (and optional SPAN parts)."""
    empty: dict[str, Any] = {
        "available_cash": None,
        "used_margin": None,
        "net": None,
        "span": None,
        "exposure": None,
        "option_premium": None,
        "utilization_pct": None,
        "ok": False,
    }
    if raw is None:
        return empty
    if not isinstance(raw, dict):
        return empty
    data = raw.get("data", raw)
    if not isinstance(data, dict):
        return empty

    available_cash: float | None = None
    used_margin: float | None = None
    net: float | None = None
    span: float | None = None
    exposure: float | None = None
    option_premium: float | None = None

    for segment in ("equity", "commodity"):
        seg = data.get(segment)
        if not isinstance(seg, dict):
            continue
        avail = seg.get("available") if isinstance(seg.get("available"), dict) else seg
        utilised = seg.get("utilised") if isinstance(seg.get("utilised"), dict) else None
        if available_cash is None:
            available_cash = _pick_number(
                avail,
                "live_balance",
                "cash",
                "net",
                "available_balance",
                "available_margin",
            )
        if isinstance(utilised, dict):
            if used_margin is None:
                used_margin = _pick_number(
                    utilised,
                    "debits",
                    "used",
                    "used_margin",
                    "total",
                )
            if span is None:
                span = _pick_number(utilised, "span")
            if exposure is None:
                exposure = _pick_number(utilised, "exposure")
            if option_premium is None:
                option_premium = _pick_number(utilised, "option_premium")
            if used_margin is None and any(v is not None for v in (span, exposure, option_premium)):
                used_margin = round(
                    (span or 0.0) + (exposure or 0.0) + (option_premium or 0.0),
                    2,
                )
        if net is None:
            net = _pick_number(seg, "net")
        if available_cash is not None or used_margin is not None:
            break

    if available_cash is None:
        available_cash = _pick_number(
            data,
            "live_balance",
            "cash",
            "net",
            "available_balance",
            "available_margin",
            "available",
        )
    if used_margin is None:
        used_margin = _pick_number(
            data,
            "used",
            "used_margin",
            "utilised",
            "debits",
        )

    utilization_pct: float | None = None
    if (
        used_margin is not None
        and available_cash is not None
        and (used_margin + available_cash) > 0
    ):
        utilization_pct = round(100.0 * used_margin / (used_margin + available_cash), 2)
    elif used_margin is not None and net is not None and net > 0:
        utilization_pct = round(100.0 * used_margin / net, 2)

    ok = available_cash is not None or used_margin is not None
    return {
        "available_cash": available_cash,
        "used_margin": used_margin,
        "net": net,
        "span": span,
        "exposure": exposure,
        "option_premium": option_premium,
        "utilization_pct": utilization_pct,
        "ok": ok,
    }


def _split_exchange_symbol(symbol: str) -> tuple[str, str]:
    raw = canonical_broker_option_symbol(symbol)
    if ":" in raw:
        exchange, tradingsymbol = raw.split(":", 1)
        return exchange.upper(), tradingsymbol.strip().upper()
    parsed = parse_option_symbol(raw)
    if parsed:
        return option_exchange(parsed["root"]), raw.upper()
    return "NFO", raw.upper()


def _round_option_price(price: float) -> float:
    """NSE option tick is typically ₹0.05."""
    if price <= 0:
        return 0.05
    # Multiply-then-round avoids float noise from price/0.05.
    return max(0.05, round(round(price * 20) / 20, 2))


def _clamp_exit_pct(value: float | None, *, kind: str) -> float | None:
    """Bound SL/target % so tick math stays meaningful (audit F3)."""
    if value is None:
        return None
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return None
    if pct <= 0:
        return None
    # Long SL at 100% collapses to ₹0.05 floor; keep headroom above min tick.
    upper = 90.0 if kind == "stop" else 200.0
    return min(upper, max(0.5, pct))


def build_leg_exit_gtt(
    *,
    side: str,
    premium: float,
    quantity: int,
    exchange: str,
    tradingsymbol: str,
    product: str,
    stop_loss_pct: float | None,
    target_pct: float | None,
) -> dict[str, Any] | None:
    """Build Kite GTT kwargs for a per-leg premium SL and/or target exit.

    Long (buy): SL below entry, target above — exit SELL.
    Short (sell): SL above entry, target below — exit BUY.

    Zerodha F&O GTT supports NRML only — callers should skip MIS.

    Returns optional ``notes`` / ``dropped`` when tick rounding cannot honor
    the requested percentages (low-premium legs).
    """
    if quantity < 1 or premium <= 0:
        return None
    if str(product or "").upper() == "MIS":
        return None
    sl_req = stop_loss_pct if stop_loss_pct is not None and stop_loss_pct > 0 else None
    tgt_req = target_pct if target_pct is not None and target_pct > 0 else None
    sl = _clamp_exit_pct(sl_req, kind="stop")
    tgt = _clamp_exit_pct(tgt_req, kind="target")
    if sl is None and tgt is None:
        return None

    is_long = str(side or "buy").lower() == "buy"
    exit_txn = "SELL" if is_long else "BUY"
    last_price = _round_option_price(premium)
    triggers: list[float] = []
    orders: list[dict[str, Any]] = []
    roles: list[str] = []
    notes: list[str] = []
    dropped: list[str] = []

    def _add(level: float, *, below: bool, role: str, requested_pct: float) -> None:
        px = _round_option_price(level)
        if abs(px - last_price) < 1e-9:
            px = _round_option_price(last_price - 0.05 if below else last_price + 0.05)
        if px <= 0 or abs(px - last_price) < 1e-9:
            dropped.append(role)
            notes.append(
                f"{role} dropped — premium ₹{last_price:.2f} leaves no tick room "
                f"for {requested_pct:g}%."
            )
            return
        if any(abs(px - t) < 1e-9 for t in triggers):
            dropped.append(role)
            notes.append(f"{role} dropped — collided with another trigger after tick round.")
            return
        eff = abs(px - last_price) / last_price * 100.0
        if abs(eff - requested_pct) > 1.0:
            notes.append(
                f"{role} distorted by tick: requested {requested_pct:g}% → effective {eff:.1f}% "
                f"(₹{px:.2f})."
            )
        triggers.append(px)
        roles.append(role)
        orders.append(
            {
                "exchange": exchange.upper(),
                "tradingsymbol": tradingsymbol,
                "transaction_type": exit_txn,
                "quantity": int(quantity),
                "order_type": "LIMIT",
                "product": "NRML",
                "price": px,
            }
        )

    if sl is not None:
        if is_long:
            _add(premium * (1 - sl / 100.0), below=True, role="stop_loss", requested_pct=sl)
        else:
            _add(premium * (1 + sl / 100.0), below=False, role="stop_loss", requested_pct=sl)
    if tgt is not None:
        if is_long:
            _add(premium * (1 + tgt / 100.0), below=False, role="target", requested_pct=tgt)
        else:
            _add(premium * (1 - tgt / 100.0), below=True, role="target", requested_pct=tgt)

    if not triggers:
        return None
    # Kite two-leg samples always use ascending trigger_values [low, high]
    # with orders[] index-aligned (see kiteconnect gtt_order.py OCO example).
    if len(triggers) == 2 and triggers[0] > triggers[1]:
        triggers = [triggers[1], triggers[0]]
        orders = [orders[1], orders[0]]
        roles = [roles[1], roles[0]]
    trigger_type = "two-leg" if len(triggers) == 2 else "single"
    out: dict[str, Any] = {
        "tradingsymbol": tradingsymbol,
        "exchange": exchange.upper(),
        "trigger_type": trigger_type,
        "trigger_values": triggers,
        "last_price": last_price,
        "orders": orders,
        "roles": roles,
    }
    if notes:
        out["notes"] = notes
    if dropped:
        out["dropped"] = dropped
    if sl is not None and sl_req is not None and abs(sl - sl_req) > 1e-9:
        notes.append(f"stop_loss_pct clamped {sl_req:g}% → {sl:g}%.")
        out["notes"] = notes
    if tgt is not None and tgt_req is not None and abs(tgt - tgt_req) > 1e-9:
        notes.append(f"target_pct clamped {tgt_req:g}% → {tgt:g}%.")
        out["notes"] = notes
    return out


def _gtt_trigger_id(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (str, int)):
        return raw
    if not isinstance(raw, dict):
        return None
    data = raw.get("data", raw)
    if isinstance(data, dict):
        return data.get("trigger_id") or data.get("id")
    return raw.get("trigger_id") or raw.get("id")


class OptionsLabTradingService:
    def __init__(self, session: AsyncSession, context: Any) -> None:
        self.session = session
        self.context = context
        self.teams = TeamRepository(session, context)
        self.tools = ToolDefinitionRepository(session, context)
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
        basket: bool = False,
    ) -> dict[str, Any]:
        if not legs:
            return {"ok": False, "error": "No legs."}

        available = None
        margin_needed = None
        margin_final = None
        source = "heuristic"
        used_basket = False
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

            order_items: list[dict[str, Any]] = []
            skipped_build = 0
            for leg in legs:
                item = _leg_to_margin_order(leg, lot_size=lot_size, product=product)
                if item is None:
                    skipped_build += 1
                    continue
                order_items.append(item)

            prefer_basket = basket or len(order_items) >= 2
            if prefer_basket and order_items and skipped_build == 0:
                basket_fn, basket_name, _ = await self._find_tool(
                    ("get_basket_margins",),
                    team_slugs=(SIGNAL_TEAM_SLUG, *DESK_TEAM_SLUGS),
                )
                if basket_fn is not None:
                    try:
                        raw = await invoke_tool(
                            basket_fn,
                            {
                                "orders": order_items,
                                "consider_positions": True,
                            },
                        )
                        init_total, final_total = _basket_margin_totals(raw)
                        if init_total is not None:
                            margin_needed = round(init_total, 2)
                            margin_final = (
                                round(final_total, 2) if final_total is not None else None
                            )
                            source = basket_name or "get_basket_margins"
                            used_basket = True
                            if (
                                margin_final is not None
                                and margin_needed is not None
                                and margin_final + 1 < margin_needed
                            ):
                                warnings.append(
                                    f"Basket SPAN initial ₹{margin_needed:,.0f} "
                                    f"(hedged final ₹{margin_final:,.0f}) — "
                                    "use initial when funding the place."
                                )
                        else:
                            warnings.append(
                                f"{basket_name or 'get_basket_margins'} returned no initial total."
                            )
                    except Exception as exc:  # noqa: BLE001
                        warnings.append(f"Basket margins failed: {exc}")

            if margin_needed is None:
                order_fn, order_name, _ = await self._find_tool(
                    ("get_order_margins", "get_required_margin"),
                    team_slugs=(SIGNAL_TEAM_SLUG, *DESK_TEAM_SLUGS),
                )
                if order_fn is not None:
                    totals: list[float] = []
                    priced_legs = 0
                    skipped_legs = 0
                    for leg in legs:
                        item = _leg_to_margin_order(leg, lot_size=lot_size, product=product)
                        if item is None:
                            skipped_legs += 1
                            warnings.append("Margin skipped invalid leg (symbol/qty).")
                            continue
                        kwargs: dict[str, Any] = {
                            "exchange": item["exchange"],
                            "tradingsymbol": item["tradingsymbol"],
                            "transaction_type": item["transaction_type"],
                            "variety": item["variety"],
                            "product": item["product"],
                            "order_type": item["order_type"],
                            "quantity": item["quantity"],
                            "price": item["price"],
                        }
                        if order_name == "get_required_margin":
                            kwargs = {
                                "exchange": item["exchange"],
                                "trading_symbol": item["tradingsymbol"],
                                "transaction_type": item["transaction_type"],
                                "quantity": item["quantity"],
                                "product": item["product"],
                                "segment": "FNO",
                                "order_type": item["order_type"],
                                "price": item["price"],
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
                                    f"Order margin for {item['tradingsymbol']} returned no total."
                                )
                        except Exception as exc:  # noqa: BLE001
                            skipped_legs += 1
                            warnings.append(
                                f"Order margin for {item['tradingsymbol']} failed: {exc}"
                            )
                    incomplete = skipped_legs > 0 or priced_legs < len(legs)
                    if totals and not incomplete:
                        margin_needed = round(sum(totals), 2)
                        source = order_name or "broker_margins"
                        if len(legs) >= 2:
                            warnings.append(
                                "Broker margins are summed per leg — not exchange basket SPAN. "
                                "Bind get_basket_margins for hedge benefit."
                            )
                    elif totals and incomplete:
                        warnings.append(
                            f"Broker margins incomplete ({priced_legs}/{len(legs)} legs) — "
                            "using heuristic."
                        )
                        margin_needed = None
                        source = "heuristic"

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
            "margin_final": margin_final,
            "margin_available": available,
            "lot_size": lot_size,
            "product": product.upper(),
            "basket": used_basket,
            "estimated": source in {"heuristic", "mock_heuristic"},
            "warnings": warnings,
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
        stop_loss_pct: float | None = None,
        target_pct: float | None = None,
        basket: bool = False,
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

        want_gtt = (stop_loss_pct is not None and stop_loss_pct > 0) or (
            target_pct is not None and target_pct > 0
        )
        order_type_u = str(order_type or "LIMIT").upper()
        # Audit F1: LIMIT accept ≠ fill — auto GTT on accept can naked-short if entry never fills.
        gtt_allowed_for_order = order_type_u == "MARKET"

        if mock:
            mock_orders = [
                {
                    "symbol": leg.get("symbol"),
                    "side": leg.get("side"),
                    "status": "simulated",
                    "order_id": f"mock-{idx}",
                }
                for idx, leg in enumerate(legs)
            ]
            mock_gtts: list[dict[str, Any]] = []
            warnings = ["Mock mode — no broker orders sent."]
            if want_gtt:
                if str(product or "").upper() == "MIS":
                    warnings.append(
                        "Mock SL/Tgt GTT skipped — Zerodha F&O GTT supports NRML only (not MIS)."
                    )
                elif not gtt_allowed_for_order:
                    warnings.append(
                        "Mock SL/Tgt GTT skipped — auto GTT only for MARKET entries "
                        "(LIMIT accept ≠ fill)."
                    )
                else:
                    for idx, leg in enumerate(legs):
                        symbol = str(leg.get("symbol") or "").strip()
                        if not symbol:
                            continue
                        exchange, tradingsymbol = _split_exchange_symbol(symbol)
                        try:
                            premium = float(leg.get("entry_premium") or leg.get("premium") or 0)
                        except (TypeError, ValueError):
                            premium = 0.0
                        qty_lots = _leg_qty_lots(leg) or 0
                        quantity = int(round(qty_lots * lot_size))
                        plan = build_leg_exit_gtt(
                            side=str(leg.get("side") or "buy"),
                            premium=premium,
                            quantity=quantity,
                            exchange=exchange,
                            tradingsymbol=tradingsymbol,
                            product=product,
                            stop_loss_pct=stop_loss_pct,
                            target_pct=target_pct,
                        )
                        if plan:
                            mock_gtts.append(
                                {
                                    "leg_index": idx,
                                    "symbol": f"{exchange}:{tradingsymbol}",
                                    "status": "simulated",
                                    "trigger_id": f"mock-gtt-{idx}",
                                    "trigger_type": plan["trigger_type"],
                                    "trigger_values": plan["trigger_values"],
                                    "roles": plan.get("roles"),
                                    "dropped": plan.get("dropped"),
                                    "notes": plan.get("notes"),
                                }
                            )
                            for note in plan.get("notes") or []:
                                warnings.append(f"Leg {idx}: {note}")
                    warnings.append(
                        "Mock SL/Tgt GTT exits simulated only — live place_gtt runs after "
                        "MARKET place_order accept."
                    )
            return {
                "ok": True,
                "mock": True,
                "orders": mock_orders,
                "gtts": mock_gtts,
                "warnings": warnings,
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
        # Basket mode: concurrent buy wave, then concurrent sell wave (Kite has no atomic basket place).
        ordered = sorted(
            enumerate(legs),
            key=lambda item: (
                0 if str(item[1].get("side") or "buy").lower() == "buy" else 1,
                item[0],
            ),
        )

        async def _place_one(idx: int, leg: dict[str, Any]) -> dict[str, Any]:
            side = str(leg.get("side") or "buy").lower()
            symbol = str(leg.get("symbol") or "").strip()
            if not symbol:
                return {
                    "leg_index": idx,
                    "symbol": None,
                    "side": leg.get("side"),
                    "status": "error",
                    "error": "missing symbol",
                    "_leg": leg,
                }
            exchange, tradingsymbol = _split_exchange_symbol(symbol)
            txn = "BUY" if side == "buy" else "SELL"
            qty_lots = _leg_qty_lots(leg)
            if qty_lots is None:
                return {
                    "leg_index": idx,
                    "symbol": f"{exchange}:{tradingsymbol}",
                    "side": side,
                    "status": "error",
                    "error": "qty must be > 0",
                    "_leg": leg,
                }
            quantity = int(round(qty_lots * lot_size))
            if quantity < 1:
                return {
                    "leg_index": idx,
                    "symbol": f"{exchange}:{tradingsymbol}",
                    "side": side,
                    "status": "error",
                    "error": "quantity rounds to 0",
                    "_leg": leg,
                }
            try:
                premium = float(leg.get("entry_premium") or leg.get("premium") or 0)
            except (TypeError, ValueError):
                premium = 0.0
            ot = order_type.upper()
            if ot == "LIMIT" and not (premium > 0):
                return {
                    "leg_index": idx,
                    "symbol": f"{exchange}:{tradingsymbol}",
                    "side": side,
                    "quantity": quantity,
                    "status": "error",
                    "error": "LIMIT requires premium > 0",
                    "_leg": leg,
                }
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
                    return {
                        "leg_index": idx,
                        "symbol": f"{exchange}:{tradingsymbol}",
                        "side": side,
                        "quantity": quantity,
                        "status": "rejected",
                        "error": reject_reason or "rejected",
                        "raw": raw,
                        "_leg": leg,
                    }
                return {
                    "leg_index": idx,
                    "symbol": f"{exchange}:{tradingsymbol}",
                    "side": side,
                    "quantity": quantity,
                    "status": "submitted",
                    "order_id": order_id,
                    "raw": raw,
                    "_leg": leg,
                }
            except Exception as exc:  # noqa: BLE001
                return {
                    "leg_index": idx,
                    "symbol": f"{exchange}:{tradingsymbol}",
                    "side": side,
                    "quantity": quantity,
                    "status": "error",
                    "error": str(exc),
                    "_leg": leg,
                }

        results: list[dict[str, Any]] = []
        errors: list[str] = []
        submitted_legs: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
        warnings: list[str] = []

        buy_wave = [
            (idx, leg)
            for idx, leg in ordered
            if str(leg.get("side") or "buy").lower() == "buy"
        ]
        sell_wave = [
            (idx, leg)
            for idx, leg in ordered
            if str(leg.get("side") or "buy").lower() != "buy"
        ]

        async def _run_wave(
            wave: list[tuple[int, dict[str, Any]]],
            *,
            concurrent: bool,
        ) -> list[dict[str, Any]]:
            if not wave:
                return []
            if concurrent and len(wave) > 1:
                return list(await asyncio.gather(*[_place_one(i, leg) for i, leg in wave]))
            out: list[dict[str, Any]] = []
            for i, leg in wave:
                out.append(await _place_one(i, leg))
            return out

        def _ingest(rows: list[dict[str, Any]]) -> bool:
            """Append rows; return True if any buy in this wave failed."""
            any_buy_fail = False
            for row in rows:
                leg = row.pop("_leg", {})
                idx = int(row.get("leg_index") or 0)
                side = str(row.get("side") or "buy").lower()
                results.append(row)
                if row.get("status") == "submitted":
                    submitted_legs.append((idx, leg, row))
                else:
                    err = row.get("error") or row.get("status") or "failed"
                    sym = row.get("symbol") or f"leg {idx}"
                    errors.append(f"{sym}: {err}")
                    if side == "buy":
                        any_buy_fail = True
            return any_buy_fail

        if basket:
            warnings.append(
                "Basket place: concurrent buy wave then sell wave "
                "(Kite has no atomic multi-leg place API)."
            )
            buy_failed = _ingest(await _run_wave(buy_wave, concurrent=True))
            if buy_failed:
                for idx, leg in sell_wave:
                    side = str(leg.get("side") or "sell").lower()
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
            else:
                _ingest(await _run_wave(sell_wave, concurrent=True))
        else:
            buy_failed = False
            for idx, leg in ordered:
                side = str(leg.get("side") or "buy").lower()
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
                row = await _place_one(idx, leg)
                if _ingest([row]) and side == "buy":
                    buy_failed = True

        submitted = [r for r in results if r.get("status") == "submitted"]
        failed = [r for r in results if r.get("status") != "submitted"]
        partial = bool(submitted) and bool(failed)
        if partial:
            warnings.append(
                "PARTIAL FILL — some legs submitted. Check positions before Buy again "
                "(re-sending all legs can double the book)."
            )

        gtts: list[dict[str, Any]] = []
        if want_gtt and submitted_legs:
            product_u = str(product or "NRML").upper()
            if product_u == "MIS":
                warnings.append(
                    "SL/Tgt GTT skipped — Zerodha F&O GTT supports NRML only (not MIS)."
                )
            elif place_name != "place_order":
                warnings.append(
                    "SL/Tgt GTT skipped — paper fills have no Kite GTT. "
                    "Use live place_order for broker exits."
                )
            elif not gtt_allowed_for_order:
                warnings.append(
                    "SL/Tgt GTT skipped — auto exits only for MARKET entries. "
                    "LIMIT accept ≠ fill; place GTTs manually after fill or switch to MARKET."
                )
            else:
                gtt_fn, gtt_name, gtt_team = await self._find_tool(
                    GTT_TOOL_NAMES,
                    team_slugs=("live-trading", SIGNAL_TEAM_SLUG, "paper-trading"),
                )
                if gtt_fn is None:
                    warnings.append(
                        "SL/Tgt requested but place_gtt is not bound on Live/Signals — "
                        "republish kite toolkit with place_gtt marked mutating."
                    )
                else:
                    warnings.append(
                        "GTT exits placed after MARKET place_order accept "
                        "(still cancel GTTs if an entry unexpectedly does not fill)."
                    )
                    for idx, leg, order_row in submitted_legs:
                        symbol = str(order_row.get("symbol") or "")
                        exchange, tradingsymbol = _split_exchange_symbol(symbol)
                        try:
                            premium = float(
                                leg.get("entry_premium") or leg.get("premium") or 0
                            )
                        except (TypeError, ValueError):
                            premium = 0.0
                        plan = build_leg_exit_gtt(
                            side=str(leg.get("side") or "buy"),
                            premium=premium,
                            quantity=int(order_row.get("quantity") or 0),
                            exchange=exchange,
                            tradingsymbol=tradingsymbol,
                            product=product_u,
                            stop_loss_pct=stop_loss_pct,
                            target_pct=target_pct,
                        )
                        if plan is None:
                            gtts.append(
                                {
                                    "leg_index": idx,
                                    "symbol": symbol,
                                    "status": "skipped",
                                    "error": "could not derive SL/Tgt levels from premium",
                                }
                            )
                            continue
                        for note in plan.get("notes") or []:
                            warnings.append(f"Leg {idx} {tradingsymbol}: {note}")
                        gtt_kwargs = {
                            "tradingsymbol": plan["tradingsymbol"],
                            "exchange": plan["exchange"],
                            "trigger_type": plan["trigger_type"],
                            "trigger_values": plan["trigger_values"],
                            "last_price": plan["last_price"],
                            "orders": plan["orders"],
                        }
                        try:
                            raw = await invoke_tool(gtt_fn, gtt_kwargs)
                            trigger_id = _gtt_trigger_id(raw)
                            if (
                                trigger_id is None
                                and isinstance(raw, dict)
                                and raw.get("ok") is False
                            ):
                                raise RuntimeError(
                                    str(
                                        raw.get("error")
                                        or raw.get("message")
                                        or "gtt failed"
                                    )
                                )
                            gtts.append(
                                {
                                    "leg_index": idx,
                                    "symbol": symbol,
                                    "status": "submitted",
                                    "trigger_id": trigger_id,
                                    "trigger_type": plan["trigger_type"],
                                    "trigger_values": plan["trigger_values"],
                                    "roles": plan.get("roles"),
                                    "dropped": plan.get("dropped"),
                                    "notes": plan.get("notes"),
                                    "tool": gtt_name,
                                    "team_slug": gtt_team,
                                    **({} if trigger_id is not None else {"raw": raw}),
                                }
                            )
                        except Exception as exc:  # noqa: BLE001
                            warnings.append(f"GTT {tradingsymbol}: {exc}")
                            gtts.append(
                                {
                                    "leg_index": idx,
                                    "symbol": symbol,
                                    "status": "error",
                                    "error": str(exc),
                                    "trigger_type": plan["trigger_type"],
                                    "trigger_values": plan["trigger_values"],
                                    "roles": plan.get("roles"),
                                    "dropped": plan.get("dropped"),
                                    "notes": plan.get("notes"),
                                }
                            )

        return {
            "ok": len(failed) == 0 and len(submitted) > 0,
            "partial": partial,
            "tool": place_name,
            "team_slug": team_slug,
            "orders": results,
            "gtts": gtts,
            "basket": bool(basket),
            "submitted_count": len(submitted),
            "failed_count": len(failed),
            "errors": errors,
            "warnings": warnings,
        }

    def _extract_gtt_list(self, raw: Any) -> tuple[list[Any] | None, bool]:
        """Return (rows, recognized). recognized=False → fail closed (do not fake empty)."""
        if isinstance(raw, list):
            return raw, True
        if isinstance(raw, dict):
            for key in ("data", "triggers", "gtts", "result"):
                if key in raw:
                    val = raw[key]
                    if isinstance(val, list):
                        return val, True
                    # Key present but not a list (e.g. null / object) — treat empty-null as empty book.
                    if val is None:
                        return [], True
                    return None, False
            return None, False
        if raw is None:
            return [], True
        return None, False

    def _normalize_gtt_rows(self, raw: Any) -> list[dict[str, Any]]:
        payload, recognized = self._extract_gtt_list(raw)
        if not recognized or payload is None:
            return []
        rows: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            condition = item.get("condition")
            if isinstance(condition, str):
                try:
                    parsed = json.loads(condition)
                    condition = parsed if isinstance(parsed, dict) else {}
                except Exception:  # noqa: BLE001
                    condition = {}
            if not isinstance(condition, dict):
                condition = {}
            trigger_id = item.get("id") or item.get("trigger_id")
            orders = item.get("orders") if isinstance(item.get("orders"), list) else []
            symbols: list[str] = []
            for order in orders:
                if isinstance(order, dict) and order.get("tradingsymbol"):
                    symbols.append(str(order["tradingsymbol"]))
            rows.append(
                {
                    "trigger_id": trigger_id,
                    "status": item.get("status"),
                    "type": item.get("type") or condition.get("type"),
                    "trigger_values": condition.get("trigger_values") or item.get("trigger_values"),
                    "tradingsymbol": condition.get("tradingsymbol")
                    or (symbols[0] if symbols else None),
                    "symbols": symbols,
                    "created_at": item.get("created_at") or item.get("created"),
                }
            )
        return rows

    async def list_gtts(self, *, mock: bool = False) -> dict[str, Any]:
        if mock:
            return {
                "ok": True,
                "mock": True,
                "gtts": [],
                "warnings": ["Mock mode — GTT list is empty (no broker call)."],
            }
        list_fn, list_name, team_slug = await self._find_tool(
            GTT_LIST_TOOL_NAMES,
            team_slugs=(SIGNAL_TEAM_SLUG, *DESK_TEAM_SLUGS),
        )
        if list_fn is None:
            return {
                "ok": False,
                "error": "list_gtts is not bound on Live/Signals — republish kite toolkit.",
                "gtts": [],
            }
        try:
            raw = await invoke_tool(list_fn, {})
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "gtts": [], "tool": list_name}
        payload, recognized = self._extract_gtt_list(raw)
        if not recognized:
            return {
                "ok": False,
                "error": "list_gtts returned an unrecognized payload shape.",
                "gtts": [],
                "tool": list_name,
                "team_slug": team_slug,
            }
        return {
            "ok": True,
            "tool": list_name,
            "team_slug": team_slug,
            "gtts": self._normalize_gtt_rows(payload if payload is not None else []),
        }

    async def delete_gtt(self, trigger_id: str | int, *, mock: bool = False) -> dict[str, Any]:
        if mock:
            return {
                "ok": True,
                "mock": True,
                "trigger_id": trigger_id,
                "warnings": ["Mock mode — delete_gtt skipped (no broker call)."],
            }
        try:
            tid = int(trigger_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Invalid trigger_id."}
        delete_fn, delete_name, team_slug = await self._find_tool(
            GTT_DELETE_TOOL_NAMES,
            team_slugs=(SIGNAL_TEAM_SLUG, *DESK_TEAM_SLUGS),
        )
        if delete_fn is None:
            return {
                "ok": False,
                "error": "delete_gtt is not bound on Live/Signals — republish kite toolkit "
                "with delete_gtt marked mutating.",
            }
        try:
            raw = await invoke_tool(delete_fn, {"trigger_id": tid})
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "error": str(exc),
                "tool": delete_name,
                "trigger_id": tid,
            }
        if isinstance(raw, dict) and raw.get("ok") is False:
            return {
                "ok": False,
                "error": str(raw.get("error") or raw.get("message") or "delete_gtt failed"),
                "tool": delete_name,
                "trigger_id": tid,
                "result": raw,
            }
        status = str((raw or {}).get("status") if isinstance(raw, dict) else "").lower()
        if status in {"error", "failed", "failure"}:
            return {
                "ok": False,
                "error": str(
                    (raw.get("error") if isinstance(raw, dict) else None)
                    or (raw.get("message") if isinstance(raw, dict) else None)
                    or f"delete_gtt status={status}"
                ),
                "tool": delete_name,
                "trigger_id": tid,
                "result": raw,
            }
        return {
            "ok": True,
            "tool": delete_name,
            "team_slug": team_slug,
            "trigger_id": tid,
            "result": raw,
        }
