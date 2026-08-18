"""Desk snapshot book normalization (broker-agnostic rows)."""

from __future__ import annotations

from app.domains.desk_snapshot import (
    CORE_BOOK_TABS,
    DEFAULT_WATCHLIST,
    READ_CAPABILITIES,
    broker_display_name,
    assemble_books,
    call_kwargs,
    empty_books,
    normalize_funds,
    normalize_holdings,
    normalize_orders,
    normalize_positions,
    normalize_watchlist,
    quote_call_attempts,
    watchlist_symbols,
)


def test_broker_display_name_fixes_groww_tookit_typo() -> None:
    assert broker_display_name("groww_tookit", "groww-tookit") == "groww_toolkit"
    assert broker_display_name("groww_toolkit", "groww-toolkit") == "groww_toolkit"
    assert broker_display_name("GrowMCP", "growmcp") == "GrowMCP"


def test_empty_books_include_core_tabs_even_without_tools() -> None:
    books = empty_books(has_tools=False)
    tabs = [book["tab"] for book in books]
    assert list(CORE_BOOK_TABS) == ["orders", "positions", "holdings", "watchlist"]
    for tab in CORE_BOOK_TABS:
        assert tab in tabs
    assert "funds" in tabs
    orders = next(book for book in books if book["id"] == "orders")
    assert orders["rows"] == []
    assert orders["columns"]
    assert "Refresh" in orders["empty_hint"] or "Bind" in orders["empty_hint"]
    watchlist = next(book for book in books if book["id"] == "watchlist")
    assert [row["symbol"] for row in watchlist["rows"]] == list(DEFAULT_WATCHLIST)


def test_assemble_books_keeps_empty_tabs_when_capability_missing() -> None:
    books = assemble_books({}, has_tools=True)
    tabs = {book["tab"] for book in books}
    assert {"orders", "positions", "holdings", "funds"} <= tabs
    assert "trades" not in tabs
    orders = next(book for book in books if book["id"] == "orders")
    assert orders["rows"] == []
    assert orders["error"] is None


def test_normalize_orders_from_ok_data_payload() -> None:
    rows = normalize_orders(
        {
            "ok": True,
            "data": [
                {
                    "trading_symbol": "RELIANCE",
                    "transaction_type": "BUY",
                    "quantity": 10,
                    "status": "COMPLETE",
                    "price": 1400.5,
                    "product": "CNC",
                    "order_timestamp": "2026-08-13T10:00:00",
                    "groww_order_id": "G1",
                }
            ],
        }
    )
    assert rows == [
        {
            "symbol": "RELIANCE",
            "side": "BUY",
            "qty": 10,
            "status": "COMPLETE",
            "price": 1400.5,
            "product": "CNC",
            "time": "2026-08-13T10:00:00",
            "order_id": "G1",
        }
    ]


def test_normalize_orders_from_kite_fields() -> None:
    rows = normalize_orders(
        {
            "ok": True,
            "data": [
                {
                    "tradingsymbol": "INFY",
                    "transaction_type": "SELL",
                    "quantity": 2,
                    "status": "OPEN",
                    "price": 1500,
                    "product": "MIS",
                    "order_timestamp": "10:01:00",
                    "order_id": "K1",
                }
            ],
        }
    )
    assert rows[0]["symbol"] == "INFY"
    assert rows[0]["side"] == "SELL"
    assert rows[0]["order_id"] == "K1"


def test_normalize_positions_from_kite_net_book() -> None:
    rows = normalize_positions(
        {
            "ok": True,
            "data": {
                "net": [
                    {
                        "tradingsymbol": "SBIN",
                        "quantity": -5,
                        "average_price": 800,
                        "last_price": 790,
                        "pnl": -50,
                        "product": "MIS",
                    }
                ],
                "day": [],
            },
        }
    )
    assert rows[0]["symbol"] == "SBIN"
    assert rows[0]["qty"] == -5
    assert rows[0]["pnl"] == -50


def test_normalize_holdings_and_watchlist_symbols() -> None:
    holdings = {
        "ok": True,
        "data": [{"trading_symbol": "TCS", "quantity": 1, "average_price": 3500}],
    }
    rows = normalize_holdings(holdings)
    assert rows[0]["symbol"] == "TCS"
    symbols = watchlist_symbols(holdings=holdings, positions=None)
    assert symbols[:3] == list(DEFAULT_WATCHLIST)
    assert "TCS" in symbols


def test_normalize_funds_picks_available_used_net() -> None:
    rows = normalize_funds(
        {
            "ok": True,
            "data": {
                "equity": {
                    "net": 12000,
                    "available": {"cash": 10000, "collateral": 2000},
                    "utilised": {"debits": 500},
                }
            },
        }
    )
    labels = [row["label"] for row in rows]
    assert "available" in labels or "net" in labels
    assert any(row["value"] == 12000 for row in rows)


def test_normalize_watchlist_from_quote_map() -> None:
    rows = normalize_watchlist(
        {
            "ok": True,
            "data": {
                "NSE:RELIANCE": {"last_price": 1401.2, "net_change": 4.5},
            },
        },
        ["NSE:NIFTY", "NSE:RELIANCE"],
    )
    by_symbol = {row["symbol"]: row for row in rows}
    assert by_symbol["NSE:RELIANCE"]["ltp"] == 1401.2
    assert by_symbol["NSE:NIFTY"]["ltp"] is None


def test_quote_kwargs_for_groww_and_kite_signatures() -> None:
    async def get_quote(exchange: str, segment: str, trading_symbols: str):
        return exchange, segment, trading_symbols

    async def get_ltp(segment: str, exchange_symbols: str):
        return segment, exchange_symbols

    async def kite_quote(instruments: str):
        return instruments

    groww = quote_call_attempts(
        get_quote, ["NSE:RELIANCE", "NSE:NIFTY", "NFO:NIFTY26AUGFUT"]
    )
    assert groww[0] == {
        "trading_symbols": "RELIANCE,NIFTY",
        "exchange": "NSE",
        "segment": "CASH",
    }
    assert groww[1] == {
        "trading_symbols": "NIFTY26AUGFUT",
        "exchange": "NSE",
        "segment": "FNO",
    }

    groww_ltp = quote_call_attempts(
        get_ltp,
        [
            "NSE:NIFTY",
            "NFO:NIFTY26AUG24500CE",
            "NFO:NIFTY26AUG24500PE",
        ],
    )
    assert groww_ltp[0] == {
        "segment": "CASH",
        "exchange_symbols": "NSE_NIFTY",
    }
    assert groww_ltp[1] == {
        "segment": "FNO",
        "exchange_symbols": "NSE_NIFTY26AUG24500CE,NSE_NIFTY26AUG24500PE",
    }

    kite = quote_call_attempts(kite_quote, ["NSE:RELIANCE"])
    assert kite[0]["instruments"] == "NSE:RELIANCE"


def test_call_kwargs_fills_user_id_for_paper_positions() -> None:
    async def list_positions(user_id: str, mode: str = "paper"):
        return user_id, mode

    assert call_kwargs(list_positions, user_id="trader-1") == {"user_id": "trader-1"}
    assert call_kwargs(list_positions, user_id=None) is None


def test_read_capabilities_include_trades_and_paper_positions() -> None:
    assert "list_trades" in READ_CAPABILITIES
    assert "list_positions" in READ_CAPABILITIES
