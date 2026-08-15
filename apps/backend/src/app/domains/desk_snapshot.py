"""Load broker snapshots through the desk team → agent → assigned tool path."""

from __future__ import annotations

import inspect
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.factory import AgentFactoryService, McpToolSkipped
from app.db.models import AgentToolBinding, TeamToolBinding
from app.db.repositories import AgentRepository, TeamRepository, ToolDefinitionRepository
from app.tenancy.context import TenantContext

DESK_TEAM_SLUGS = ("live-trading", "paper-trading")
READ_CAPABILITIES = {
    "get_holdings": ("Holdings", "desk_holdings"),
    "get_positions": ("Positions", "desk_positions"),
    "list_positions": ("Positions", "desk_positions"),
    "get_user_margin": ("Margin", "desk_margin"),
    "get_margins": ("Margin", "desk_margin"),
    "get_user_margins": ("Margin", "desk_margin"),
    "get_funds": ("Margin", "desk_margin"),
    "list_orders": ("Orders", "desk_orders"),
    "get_orders": ("Orders", "desk_orders"),
    "list_trades": ("Trades", "desk_trades"),
    "get_account_health": ("Account health", "desk_health"),
}
QUOTE_CAPABILITIES = ("get_quote", "get_ltp", "get_ohlc")
MUTATING_MARKERS = (
    "place_",
    "cancel_",
    "modify_",
    "create_",
    "update_",
    "delete_",
    "arm",
    "disarm",
    "publish",
    "approve",
)

CAPABILITY_BOOK = {
    "list_orders": "orders",
    "get_orders": "orders",
    "get_positions": "positions",
    "list_positions": "positions",
    "get_holdings": "holdings",
    "get_user_margin": "funds",
    "get_margins": "funds",
    "get_user_margins": "funds",
    "get_funds": "funds",
    "list_trades": "trades",
}

DEFAULT_WATCHLIST = ("NSE:NIFTY", "NSE:BANKNIFTY", "NSE:RELIANCE")
WATCHLIST_LIMIT = 12

ORDER_COLUMNS = ["symbol", "side", "qty", "status", "price", "product", "time", "order_id"]
POSITION_COLUMNS = ["symbol", "qty", "avg", "ltp", "pnl", "product"]
HOLDING_COLUMNS = ["symbol", "qty", "avg", "ltp", "pnl"]
FUNDS_COLUMNS = ["label", "value"]
WATCHLIST_COLUMNS = ["symbol", "ltp", "change"]
TRADE_COLUMNS = ["symbol", "side", "qty", "price", "time", "trade_id"]

_LIST_KEYS = (
    "data",
    "payload",
    "holdings",
    "positions",
    "orders",
    "order_list",
    "orderList",
    "trades",
    "quotes",
    "result",
    "items",
    "records",
)

CORE_BOOK_TABS = ("orders", "positions", "holdings", "watchlist")


def broker_display_name(name: str, slug: str = "") -> str:
    """Correct known desk-copy typos without renaming the stored tool."""
    if name == "groww_tookit" or slug == "groww-tookit":
        return "groww_toolkit"
    return name


class DeskSnapshotService:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self.session = session
        self.context = context
        self.teams = TeamRepository(session, context)
        self.agents = AgentRepository(session, context)
        self.tools = ToolDefinitionRepository(session, context)

    async def assigned_tools(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for team, version, binding, source in await self._iter_desk_bindings():
            definition_id = binding.tool_definition_id
            key = str(definition_id) if definition_id else f"key:{binding.tool_key}"
            if key in seen:
                continue
            seen.add(key)
            definition = (
                await self.tools.get(definition_id) if definition_id else None
            )
            rows.append(
                {
                    "id": key,
                    "slug": definition.slug if definition else (binding.tool_key or "tool"),
                    "name": (
                        broker_display_name(definition.name, definition.slug)
                        if definition
                        else (binding.tool_key or "Tool")
                    ),
                    "kind": definition.kind if definition else "builtin",
                    "active": bool(definition.active) if definition else True,
                    "published": bool(definition.published_version_id) if definition else True,
                    "connection_status": (
                        definition.connection_status if definition else "bound"
                    ),
                    "via_team": team.slug,
                    "via_team_name": team.name,
                    "via_agent": source,
                }
            )
        return rows

    async def snapshot(self) -> dict[str, Any]:
        assigned = await self.assigned_tools()
        team_meta = None
        for slug in DESK_TEAM_SLUGS:
            config = await self.teams.get_config_by_slug(slug)
            if config and config.published_version_id:
                team_meta = {
                    "id": str(config.id),
                    "slug": config.slug,
                    "name": config.name,
                }
                break
        if not assigned:
            return {
                "team": team_meta,
                "tools": [],
                "widgets": [
                    {
                        "id": "desk_broker",
                        "label": "Desk broker tools",
                        "value": "None",
                        "hint": "Assign any broker toolkit on Live trading, then refresh",
                        "group": "brokers",
                    }
                ],
                "books": empty_books(has_tools=False),
                "error": None,
            }

        factory = AgentFactoryService(self.session, self.context)
        widgets: list[dict[str, Any]] = []
        errors: list[str] = []
        seen_widgets: set[str] = set()
        captured: dict[str, dict[str, Any]] = {}
        quote_fns: list[dict[str, Any]] = []
        user_id = getattr(self.context, "user_id", None)

        for team, _version, binding, source in await self._iter_desk_bindings():
            if binding.tool_definition_id is None:
                continue
            definition = await self.tools.get(binding.tool_definition_id)
            shown = (
                broker_display_name(definition.name, definition.slug)
                if definition
                else "tool"
            )
            via = f"{team.name} → {shown}"
            if source:
                via = f"{team.name} / {source} → {shown}"
            try:
                built = await factory._build_tool(binding)
            except McpToolSkipped as exc:
                errors.append(f"{via}: {exc.user_message()}")
                continue
            except Exception as exc:  # noqa: BLE001 — surface tool errors on the desk
                errors.append(f"{via}: {exc}")
                continue
            callables = built if isinstance(built, list) else [built]
            for fn in callables:
                name = getattr(fn, "__name__", "")
                if any(marker in name for marker in MUTATING_MARKERS):
                    continue
                if name in QUOTE_CAPABILITIES:
                    quote_fns.append(
                        {
                            "fn": fn,
                            "name": name,
                            "via": via,
                            "team_slug": team.slug,
                        }
                    )
                    continue
                if name not in READ_CAPABILITIES:
                    continue
                kwargs = call_kwargs(fn, user_id=str(user_id) if user_id else None)
                if kwargs is None:
                    continue
                label, widget_id = READ_CAPABILITIES[name]
                row_id = f"{widget_id}_{binding.tool_definition_id}"
                try:
                    result = await invoke_tool(fn, kwargs)
                    result_error = payload_error(result)
                except Exception as exc:  # noqa: BLE001
                    result = None
                    result_error = str(exc)

                if row_id not in seen_widgets:
                    seen_widgets.add(row_id)
                    widgets.append(
                        {
                            "id": row_id,
                            "label": f"{label} ({shown if definition else name})",
                            "value": (
                                "Error"
                                if result_error
                                else self._summarize(result)
                            ),
                            "hint": (
                                f"Via {via}: {result_error}"[:160]
                                if result_error
                                else f"Via {via}"
                            ),
                            "group": "brokers",
                        }
                    )

                book_id = CAPABILITY_BOOK.get(name)
                if book_id and book_id not in captured:
                    captured[book_id] = {
                        "via": via,
                        "team_slug": team.slug,
                        "source": name,
                        "result": result,
                        "error": result_error,
                    }

        if not widgets:
            widgets.append(
                {
                    "id": "desk_broker",
                    "label": "Desk broker tools",
                    "value": ", ".join(row["name"] for row in assigned),
                    "hint": "Tools are assigned. Refresh after the toolkit exposes holdings/positions reads.",
                    "group": "brokers",
                }
            )

        books = assemble_books(captured, has_tools=True)
        watchlist = await self._watchlist_book(captured, quote_fns)
        books = [book for book in books if book["id"] != "watchlist"]
        # Keep core tab order: orders, positions, holdings, watchlist, then funds/trades.
        inserted = False
        ordered: list[dict[str, Any]] = []
        for book in books:
            if not inserted and book["id"] in {"funds", "trades"}:
                ordered.append(watchlist)
                inserted = True
            ordered.append(book)
        if not inserted:
            ordered.append(watchlist)
        return {
            "team": team_meta,
            "tools": assigned,
            "widgets": widgets,
            "books": ordered,
            "error": "; ".join(errors) if errors else None,
        }

    async def _watchlist_book(
        self,
        captured: dict[str, dict[str, Any]],
        quote_fns: list[dict[str, Any]],
    ) -> dict[str, Any]:
        symbols = watchlist_symbols(
            holdings=captured.get("holdings", {}).get("result"),
            positions=captured.get("positions", {}).get("result"),
        )
        live_quotes = [
            row for row in quote_fns if row.get("team_slug") == "live-trading"
        ] or quote_fns
        via = live_quotes[0]["via"] if live_quotes else None
        team_slug = live_quotes[0]["team_slug"] if live_quotes else "live-trading"
        source = None
        error = None
        rows: list[dict[str, Any]] = [{"symbol": symbol, "ltp": None, "change": None} for symbol in symbols]

        if not live_quotes:
            return make_book(
                book_id="watchlist",
                label="Watchlist",
                tab="watchlist",
                via=via,
                team_slug=team_slug,
                source=source,
                columns=list(WATCHLIST_COLUMNS),
                rows=rows,
                error=None,
                empty_hint=(
                    "Refresh after a quote-capable toolkit is bound on Live trading."
                ),
                capability_present=False,
                has_tools=True,
            )

        for candidate in live_quotes:
            attempts = quote_call_attempts(candidate["fn"], symbols)
            if not attempts:
                continue
            last_error = None
            for kwargs in attempts:
                try:
                    result = await invoke_tool(candidate["fn"], kwargs)
                    last_error = payload_error(result)
                    if last_error:
                        continue
                    quoted = normalize_watchlist(result, symbols)
                    if quoted:
                        rows = quoted
                        source = candidate["name"]
                        via = candidate["via"]
                        team_slug = candidate["team_slug"]
                        last_error = None
                        break
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
            if source:
                error = None
                break
            error = last_error

        return make_book(
            book_id="watchlist",
            label="Watchlist",
            tab="watchlist",
            via=via,
            team_slug=team_slug,
            source=source,
            columns=list(WATCHLIST_COLUMNS),
            rows=rows,
            error=error,
            empty_hint=(
                "Refresh after a quote-capable toolkit is bound on Live trading."
            ),
            capability_present=bool(source) or bool(rows),
            has_tools=True,
        )

    async def _iter_desk_bindings(
        self,
    ) -> list[tuple[Any, Any, AgentToolBinding | TeamToolBinding, str | None]]:
        found: list[tuple[Any, Any, AgentToolBinding | TeamToolBinding, str | None]] = []
        for slug in DESK_TEAM_SLUGS:
            config = await self.teams.get_config_by_slug(slug)
            if config is None or config.published_version_id is None:
                continue
            version = await self.teams.get_version(config.published_version_id)
            if version is None:
                continue
            for binding in await self.teams.bindings(version.id):
                found.append((config, version, binding, None))
            for member in await self.teams.members(version.id):
                agent_config = await self.agents.get_config(member.agent_config_id)
                if agent_config is None or agent_config.published_version_id is None:
                    continue
                agent_version = await self.agents.get_version(agent_config.published_version_id)
                if agent_version is None:
                    continue
                for binding in await self.agents.bindings(agent_version.id):
                    found.append((config, version, binding, agent_config.slug))
        return found

    @staticmethod
    def _is_zero_arg(fn: Any) -> bool:
        return is_zero_arg(fn)

    @staticmethod
    def _summarize(value: Any) -> str:
        if value is None:
            return "—"
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, str):
            return value[:80] or "—"
        if isinstance(value, list):
            return str(len(value))
        if isinstance(value, dict):
            if value.get("ok") is False:
                return str(value.get("error") or "Error")[:80]
            for key in (
                "count",
                "total",
                "available_cash",
                "net",
                "equity",
                "status",
            ):
                if key in value and value[key] is not None:
                    return str(value[key])
            for key in ("data", "holdings", "positions", "orders", "result"):
                nested = value.get(key)
                if isinstance(nested, list):
                    return str(len(nested))
            try:
                return json.dumps(value, default=str)[:80]
            except TypeError:
                return str(value)[:80]
        return str(value)[:80]


def empty_books(*, has_tools: bool) -> list[dict[str, Any]]:
    books = [
        make_book(
            book_id="orders",
            label="Orders",
            tab="orders",
            source="list_orders",
            columns=list(ORDER_COLUMNS),
            rows=[],
            empty_hint="No orders today. Place from Live trading chat, then Refresh.",
            has_tools=has_tools,
        ),
        make_book(
            book_id="positions",
            label="Positions",
            tab="positions",
            source="get_positions",
            columns=list(POSITION_COLUMNS),
            rows=[],
            empty_hint="No open positions. Refresh after Live trading fills a trade.",
            has_tools=has_tools,
        ),
        make_book(
            book_id="holdings",
            label="Holdings",
            tab="holdings",
            source="get_holdings",
            columns=list(HOLDING_COLUMNS),
            rows=[],
            empty_hint="No holdings yet. Bind a broker toolkit on Live trading, then Refresh.",
            has_tools=has_tools,
        ),
        make_book(
            book_id="watchlist",
            label="Watchlist",
            tab="watchlist",
            source="get_quote",
            columns=list(WATCHLIST_COLUMNS),
            rows=[
                {"symbol": symbol, "ltp": None, "change": None}
                for symbol in DEFAULT_WATCHLIST
            ],
            empty_hint="Refresh after a quote-capable toolkit is bound on Live trading.",
            has_tools=has_tools,
            capability_present=True,
        ),
        make_book(
            book_id="funds",
            label="Funds",
            tab="funds",
            source="get_user_margin",
            columns=list(FUNDS_COLUMNS),
            rows=[],
            empty_hint="Funds appear after a margin/funds read is bound on Live trading.",
            has_tools=has_tools,
        ),
    ]
    return books


def assemble_books(
    captured: dict[str, dict[str, Any]],
    *,
    has_tools: bool,
) -> list[dict[str, Any]]:
    specs = (
        (
            "orders",
            "Orders",
            "list_orders",
            ORDER_COLUMNS,
            normalize_orders,
            "No orders today. Place from Live trading chat, then Refresh.",
        ),
        (
            "positions",
            "Positions",
            "get_positions",
            POSITION_COLUMNS,
            normalize_positions,
            "No open positions. Refresh after Live trading fills a trade.",
        ),
        (
            "holdings",
            "Holdings",
            "get_holdings",
            HOLDING_COLUMNS,
            normalize_holdings,
            "No holdings yet. Bind a broker toolkit on Live trading, then Refresh.",
        ),
        (
            "funds",
            "Funds",
            "get_user_margin",
            FUNDS_COLUMNS,
            normalize_funds,
            "Funds appear after a margin/funds read is bound on Live trading.",
        ),
        (
            "trades",
            "Trades",
            "list_trades",
            TRADE_COLUMNS,
            normalize_trades,
            "No trades today.",
        ),
    )
    books: list[dict[str, Any]] = []
    for book_id, label, default_source, columns, normalizer, empty_hint in specs:
        hit = captured.get(book_id)
        if book_id == "trades" and hit is None:
            continue
        if hit is None:
            books.append(
                make_book(
                    book_id=book_id,
                    label=label,
                    tab=book_id,
                    source=default_source,
                    columns=list(columns),
                    rows=[],
                    empty_hint=empty_hint,
                    has_tools=has_tools,
                )
            )
            continue
        rows: list[dict[str, Any]] = []
        error = hit.get("error")
        if not error:
            try:
                rows = normalizer(hit.get("result"))
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
        books.append(
            make_book(
                book_id=book_id,
                label=label,
                tab=book_id,
                via=hit.get("via"),
                team_slug=hit.get("team_slug"),
                source=hit.get("source") or default_source,
                columns=list(columns),
                rows=rows,
                error=error,
                empty_hint=empty_hint,
                capability_present=True,
                has_tools=has_tools,
            )
        )
    return books


def make_book(
    *,
    book_id: str,
    label: str,
    tab: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    empty_hint: str,
    source: str | None = None,
    via: str | None = None,
    team_slug: str | None = None,
    error: str | None = None,
    capability_present: bool = False,
    has_tools: bool = False,
) -> dict[str, Any]:
    hint = empty_hint
    if error:
        hint = "Refresh after the broker toolkit can read this book."
    elif not rows and not capability_present:
        hint = (
            "Bind a broker toolkit on Live trading that exposes this book, then Refresh."
            if has_tools
            else "Bind a broker toolkit on Live trading, then Refresh."
        )
    return {
        "id": book_id,
        "label": label,
        "tab": tab,
        "via": via,
        "team_slug": team_slug,
        "source": source,
        "columns": columns,
        "rows": rows,
        "error": error,
        "empty_hint": hint,
    }


def unwrap_records(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return [row for row in value if row is not None]
    if not isinstance(value, dict):
        return []
    if value.get("ok") is False:
        return []
    for key in _LIST_KEYS:
        nested = value.get(key)
        if isinstance(nested, list):
            return [row for row in nested if row is not None]
        if isinstance(nested, dict):
            net = nested.get("net")
            if isinstance(net, list):
                return [row for row in net if row is not None]
            inner = unwrap_records(nested)
            if inner:
                return inner
    if _looks_like_quote_map(value):
        return _quote_map_rows(value)
    data = value.get("data")
    if isinstance(data, dict) and _looks_like_quote_map(data):
        return _quote_map_rows(data)
    return []


def payload_error(value: Any) -> str | None:
    if isinstance(value, dict) and value.get("ok") is False:
        return str(value.get("error") or "Error")[:160]
    return None


def normalize_orders(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in unwrap_records(value):
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "symbol": _pick(
                    item,
                    "symbol",
                    "trading_symbol",
                    "tradingsymbol",
                    "instrument",
                ),
                "side": _pick(
                    item, "side", "transaction_type", "transactionType", "buy_sell"
                ),
                "qty": _pick(
                    item,
                    "qty",
                    "quantity",
                    "filled_quantity",
                    "filled_qty",
                    "filledQuantity",
                ),
                "status": _pick(item, "status", "order_status", "orderStatus"),
                "price": _pick(
                    item, "price", "average_price", "averagePrice", "avg_price", "limit_price"
                ),
                "product": _pick(item, "product", "product_type", "productType"),
                "time": _pick(
                    item,
                    "time",
                    "order_timestamp",
                    "order_time",
                    "created_at",
                    "exchange_timestamp",
                ),
                "order_id": _pick(
                    item,
                    "order_id",
                    "orderId",
                    "groww_order_id",
                    "exchange_order_id",
                ),
            }
        )
    return rows


def normalize_positions(value: Any) -> list[dict[str, Any]]:
    return _normalize_lots(value, include_product=True)


def normalize_holdings(value: Any) -> list[dict[str, Any]]:
    return _normalize_lots(value, include_product=False)


def normalize_trades(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in unwrap_records(value):
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "symbol": _pick(
                    item, "symbol", "trading_symbol", "tradingsymbol", "instrument"
                ),
                "side": _pick(item, "side", "transaction_type", "transactionType"),
                "qty": _pick(item, "qty", "quantity"),
                "price": _pick(item, "price", "average_price", "averagePrice"),
                "time": _pick(
                    item, "time", "fill_timestamp", "trade_timestamp", "exchange_timestamp"
                ),
                "trade_id": _pick(item, "trade_id", "tradeId", "exchange_trade_id"),
            }
        )
    return rows


def normalize_funds(value: Any) -> list[dict[str, Any]]:
    data: Any = value
    if isinstance(value, dict):
        if value.get("ok") is False:
            return []
        nested = value.get("data")
        if isinstance(nested, dict):
            data = nested
            inner = nested.get("payload") or nested.get("margins") or nested.get("margin")
            if isinstance(inner, dict):
                data = inner
        elif "payload" in value and isinstance(value["payload"], dict):
            data = value["payload"]
    if not isinstance(data, dict):
        return []
    rows: list[dict[str, Any]] = []
    preferred = (
        ("available", ("available_cash", "available", "live_balance", "cash")),
        ("used", ("used", "utilised", "debits", "used_margin")),
        ("net", ("net", "net_margin", "equity")),
    )
    seen: set[str] = set()
    flat = _flatten_numbers(data, prefix="", depth=0)
    by_key = {key.lower(): (key, val) for key, val in flat}

    def _take(label: str, aliases: tuple[str, ...]) -> None:
        for alias in aliases:
            hit = by_key.get(alias) or by_key.get(f"equity.{alias}") or by_key.get(
                f"equity.available.{alias}"
            ) or by_key.get(f"equity.utilised.{alias}")
            if hit and hit[0] not in seen:
                seen.add(hit[0])
                rows.append({"label": label, "value": hit[1]})
                return

    for label, aliases in preferred:
        _take(label, aliases)
    for key, val in flat:
        if key in seen or len(rows) >= 16:
            break
        seen.add(key)
        rows.append({"label": key.replace(".", " "), "value": val})
    return rows


def normalize_watchlist(value: Any, symbols: list[str]) -> list[dict[str, Any]]:
    records = unwrap_records(value)
    by_symbol: dict[str, dict[str, Any]] = {}
    for item in records:
        if isinstance(item, (int, float)):
            continue
        if not isinstance(item, dict):
            continue
        symbol = str(
            _pick(
                item,
                "symbol",
                "trading_symbol",
                "tradingsymbol",
                "instrument",
                "tradingSymbol",
            )
            or ""
        )
        if not symbol:
            continue
        by_symbol[_symbol_key(symbol)] = {
            "symbol": symbol,
            "ltp": _pick(
                item,
                "ltp",
                "last_price",
                "lastPrice",
                "last_traded_price",
                "close",
            ),
            "change": _pick(
                item,
                "change",
                "net_change",
                "netChange",
                "day_change",
                "change_percent",
                "pChange",
            ),
        }
        ohlc = item.get("ohlc") if isinstance(item.get("ohlc"), dict) else None
        if ohlc and by_symbol[_symbol_key(symbol)]["ltp"] in (None, "—", ""):
            by_symbol[_symbol_key(symbol)]["ltp"] = ohlc.get("close")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for symbol in symbols:
        key = _symbol_key(symbol)
        seen.add(key)
        hit = by_symbol.get(key) or by_symbol.get(_symbol_key(_groww_symbol(symbol)))
        if hit:
            rows.append({"symbol": symbol, "ltp": hit.get("ltp"), "change": hit.get("change")})
        else:
            rows.append({"symbol": symbol, "ltp": None, "change": None})
    for key, hit in by_symbol.items():
        if key not in seen:
            rows.append(hit)
    return rows


def watchlist_symbols(*, holdings: Any, positions: Any) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    for symbol in DEFAULT_WATCHLIST:
        key = _symbol_key(symbol)
        if key in seen:
            continue
        seen.add(key)
        symbols.append(symbol)
    for row in normalize_holdings(holdings) + normalize_positions(positions):
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        key = _symbol_key(symbol)
        if key in seen:
            continue
        seen.add(key)
        symbols.append(symbol)
        if len(symbols) >= WATCHLIST_LIMIT:
            break
    return symbols


def call_kwargs(fn: Any, *, user_id: str | None) -> dict[str, Any] | None:
    if is_zero_arg(fn):
        return {}
    required = _required_params(fn)
    kwargs: dict[str, Any] = {}
    for name in required:
        if name in {"user_id", "userid"} and user_id:
            kwargs[name] = user_id
        else:
            return None
    return kwargs


def quote_call_attempts(fn: Any, symbols: list[str]) -> list[dict[str, Any]]:
    names = set(_param_names(fn))
    attempts: list[dict[str, Any]] = []
    kite_instruments = ",".join(_kite_instrument(symbol) for symbol in symbols)
    groww_symbols = ",".join(_groww_symbol(symbol) for symbol in symbols)
    unknown = not names
    if "instruments" in names or unknown:
        attempts.append({"instruments": kite_instruments})
    if "trading_symbols" in names or unknown:
        payload: dict[str, Any] = {"trading_symbols": groww_symbols}
        if "exchange" in names or unknown:
            payload["exchange"] = "NSE"
        if "segment" in names or unknown:
            payload["segment"] = "CASH"
        attempts.append(payload)
    if is_zero_arg(fn) and names:
        attempts.append({})
    # Deduplicate while preserving order.
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in attempts:
        key = json.dumps(item, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


async def invoke_tool(fn: Any, kwargs: dict[str, Any] | None = None) -> Any:
    result = fn(**(kwargs or {}))
    if inspect.isawaitable(result):
        return await result
    return result


def is_zero_arg(fn: Any) -> bool:
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    for param in signature.parameters.values():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if param.name in {"self", "ctx", "context"}:
            continue
        if param.default is inspect.Parameter.empty:
            return False
    return True


def _param_names(fn: Any) -> list[str]:
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return []
    names: list[str] = []
    for param in signature.parameters.values():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if param.name in {"self", "ctx", "context"}:
            continue
        names.append(param.name)
    return names


def _required_params(fn: Any) -> list[str]:
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return []
    required: list[str] = []
    for param in signature.parameters.values():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if param.name in {"self", "ctx", "context"}:
            continue
        if param.default is inspect.Parameter.empty:
            required.append(param.name)
    return required


def _normalize_lots(value: Any, *, include_product: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in unwrap_records(value):
        if not isinstance(item, dict):
            continue
        row: dict[str, Any] = {
            "symbol": _pick(
                item, "symbol", "trading_symbol", "tradingsymbol", "instrument"
            ),
            "qty": _pick(
                item,
                "qty",
                "quantity",
                "net_quantity",
                "netQuantity",
                "quantity_available",
            ),
            "avg": _pick(
                item,
                "avg",
                "average_price",
                "averagePrice",
                "avg_price",
                "average_buy_price",
            ),
            "ltp": _pick(item, "ltp", "last_price", "lastPrice", "close"),
            "pnl": _pick(item, "pnl", "unrealised", "unrealized", "day_pnl", "total_pnl"),
        }
        if include_product:
            row["product"] = _pick(item, "product", "product_type", "productType")
        rows.append(row)
    return rows


def _pick(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _symbol_key(symbol: str) -> str:
    return symbol.upper().replace(" ", "")


def _groww_symbol(symbol: str) -> str:
    text = symbol.strip()
    if ":" in text:
        text = text.split(":", 1)[1]
    return text.replace(" ", "")


def _kite_instrument(symbol: str) -> str:
    text = symbol.strip()
    if ":" in text:
        return text
    return f"NSE:{text}"


def _looks_like_quote_map(value: dict[str, Any]) -> bool:
    reserved = {"ok", "status", "error", "data", "payload", "message"}
    keys = [key for key in value if key not in reserved]
    if not keys:
        return False
    sample = value[keys[0]]
    return isinstance(sample, (dict, int, float))


def _quote_map_rows(value: dict[str, Any]) -> list[dict[str, Any]]:
    reserved = {"ok", "status", "error", "data", "payload", "message"}
    rows: list[dict[str, Any]] = []
    for key, item in value.items():
        if key in reserved:
            continue
        if isinstance(item, dict):
            rows.append({"symbol": key, **item})
        elif isinstance(item, (int, float)):
            rows.append({"symbol": key, "ltp": item})
    return rows


def _flatten_numbers(
    value: dict[str, Any], *, prefix: str, depth: int
) -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    if depth > 3:
        return rows
    skip = {"ok", "status", "error", "message", "note", "broker"}
    for key, item in value.items():
        if key in skip:
            continue
        label = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            rows.append((label, item))
        elif isinstance(item, str) and _looks_numeric(item):
            rows.append((label, item))
        elif isinstance(item, dict):
            rows.extend(_flatten_numbers(item, prefix=label, depth=depth + 1))
    return rows


def _looks_numeric(value: str) -> bool:
    stripped = value.replace(",", "").replace("%", "").strip()
    if not stripped:
        return False
    try:
        float(stripped)
        return True
    except ValueError:
        return False
