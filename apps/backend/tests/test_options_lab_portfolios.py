"""Tests for Options Lab draft portfolios and mark-to-market."""

from __future__ import annotations

import pytest

from app.domains import signal_engine_cache as cache
from app.domains.options_lab_portfolios import (
    canonical_broker_option_symbol,
    create_portfolio,
    delete_portfolio,
    infer_fut_symbol_from_legs,
    kite_positions_payload,
    leg_mtm,
    list_portfolios,
    mark_portfolio_legs,
    parse_option_symbol,
    positions_to_portfolio_legs,
    update_portfolio,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    cache.reset_signal_cache_for_tests()
    yield
    cache.reset_signal_cache_for_tests()


def test_parse_option_symbol() -> None:
    parsed = parse_option_symbol("NFO:NIFTY26AUG24300CE")
    assert parsed is not None
    assert parsed["strike"] == 24300
    assert parsed["type"] == "CE"
    assert parse_option_symbol("NFO:NIFTY26AUGFUT") is None


def test_parse_option_symbol_shared_fixture() -> None:
    """Pin Python parse against the shared repo fixture (also used by web)."""
    import json
    from pathlib import Path

    fixture = (
        Path(__file__).resolve().parents[3]
        / "testdata"
        / "option_symbol_parse_cases.json"
    )
    cases = json.loads(fixture.read_text())["cases"]
    assert cases, "shared fixture must not be empty"
    for case in cases:
        symbol = case["symbol"]
        parsed = parse_option_symbol(symbol)
        if case["expiry"] is None:
            assert parsed is None, symbol
            continue
        assert parsed is not None, symbol
        assert parsed["expiry"] == case["expiry"], symbol
        assert parsed["strike"] == case["strike"], symbol
        assert parsed["type"] == case["side"], symbol


def test_parse_weekly_option_symbol() -> None:
    parsed = parse_option_symbol("NIFTY2580724500CE")
    assert parsed is not None
    assert parsed["strike"] == 24500
    assert parsed["type"] == "CE"
    assert parsed["expiry"] == "25807"
    assert canonical_broker_option_symbol("NIFTY2580724500CE") == "NFO:NIFTY2580724500CE"


def test_parse_weekly_prefers_six_digit_strike() -> None:
    """6-digit strike must win over false 5-digit split (strike 0)."""
    parsed = parse_option_symbol("NIFTY25807100000CE")
    assert parsed is not None
    assert parsed["expiry"] == "25807"
    assert parsed["strike"] == 100000


def test_parse_weekly_alpha_option_symbol() -> None:
    parsed = parse_option_symbol("NIFTY25N1124500PE")
    assert parsed is not None
    assert parsed["expiry"] == "25N11"
    assert parsed["strike"] == 24500
    assert parsed["type"] == "PE"


def test_canonical_broker_option_symbol() -> None:
    assert canonical_broker_option_symbol("NIFTY26AUG24300CE") == "NFO:NIFTY26AUG24300CE"
    assert canonical_broker_option_symbol("NFO:NIFTY26AUG24300CE") == "NFO:NIFTY26AUG24300CE"


def test_infer_fut_symbol_from_legs() -> None:
    legs = [{"symbol": "NFO:NIFTY26AUG24300CE"}]
    assert infer_fut_symbol_from_legs(legs) == "NFO:NIFTY26AUGFUT"


def test_infer_fut_symbol_skips_weekly_leg_codes() -> None:
    legs = [{"symbol": "NFO:NIFTY2580724500CE"}]
    assert infer_fut_symbol_from_legs(legs) == ""


def test_leg_mtm_buy_and_sell() -> None:
    assert leg_mtm(side="buy", entry_premium=100, current_premium=120, qty=1) == 20
    assert leg_mtm(side="sell", entry_premium=100, current_premium=80, qty=2) == 40


def test_mark_portfolio_legs_uses_quotes() -> None:
    portfolio = {
        "id": "p1",
        "name": "Straddle",
        "fut_symbol": "NFO:NIFTY26AUGFUT",
        "legs": [
            {
                "id": "1",
                "side": "buy",
                "type": "CE",
                "strike": 24300,
                "qty": 1,
                "entry_premium": 100,
                "symbol": "NFO:NIFTY26AUG24300CE",
            },
            {
                "id": "2",
                "side": "buy",
                "type": "PE",
                "strike": 24300,
                "qty": 1,
                "entry_premium": 90,
                "symbol": "NFO:NIFTY26AUG24300PE",
            },
        ],
    }
    quotes = {
        "NFO:NIFTY26AUG24300CE": {"last_price": 110},
        "NFO:NIFTY26AUG24300PE": {"last_price": 85},
    }
    marked = mark_portfolio_legs(portfolio, quotes=quotes)
    assert marked["summary"]["total_mtm"] == 5
    assert marked["legs"][0]["mtm"] == 10
    assert marked["legs"][1]["mtm"] == -5


@pytest.mark.asyncio
async def test_portfolio_crud() -> None:
    tenant = "tenant-a"
    created = await create_portfolio(
        tenant,
        {
            "name": "Test spread",
            "underlying_symbol": "NSE:NIFTY 50",
            "fut_symbol": "NFO:NIFTY26AUGFUT",
            "source": "builder",
            "legs": [
                {
                    "side": "buy",
                    "type": "CE",
                    "strike": 24300,
                    "qty": 1,
                    "entry_premium": 120,
                }
            ],
        },
    )
    assert created["ok"] is True
    portfolio_id = created["portfolio"]["id"]

    listed = await list_portfolios(tenant)
    assert listed["count"] == 1

    updated = await update_portfolio(tenant, portfolio_id, {"name": "Renamed"})
    assert updated["ok"] is True
    assert updated["portfolio"]["name"] == "Renamed"

    deleted = await delete_portfolio(tenant, portfolio_id)
    assert deleted["ok"] is True
    assert (await list_portfolios(tenant))["count"] == 0


def test_kite_positions_import_maps_option_legs() -> None:
    raw = {
        "net": [
            {
                "tradingsymbol": "NIFTY26AUG24300CE",
                "quantity": 50,
                "average_price": 118.5,
                "last_price": 121.0,
            },
            {
                "tradingsymbol": "NIFTY26AUG24200PE",
                "quantity": -25,
                "average_price": 42.0,
                "last_price": 39.5,
            },
        ]
    }
    legs, warnings = kite_positions_payload(raw)
    assert not warnings
    assert len(legs) == 2
    assert legs[0]["symbol"] == "NFO:NIFTY26AUG24300CE"
    assert legs[0]["side"] == "buy"
    assert legs[0]["strike"] == 24300
    assert legs[1]["side"] == "sell"
    assert legs[1]["type"] == "PE"

    mapped = positions_to_portfolio_legs(
        [
            {"symbol": "NFO:NIFTY26AUG24300CE", "qty": 1, "avg": 100},
        ]
    )
    assert mapped[0]["entry_premium"] == 100


def test_kite_positions_import_includes_weekly_options() -> None:
    raw = {
        "net": [
            {"tradingsymbol": "NIFTY26AUG24300CE", "quantity": 50, "average_price": 118.5},
            {"tradingsymbol": "NIFTY2580724500CE", "quantity": 25, "average_price": 95.0},
            {"tradingsymbol": "NIFTY26AUGFUT", "quantity": 50, "average_price": 24300.0},
        ]
    }
    legs, warnings = kite_positions_payload(raw)
    assert not warnings
    assert len(legs) == 2
    symbols = {leg["symbol"] for leg in legs}
    assert "NFO:NIFTY26AUG24300CE" in symbols
    assert "NFO:NIFTY2580724500CE" in symbols
