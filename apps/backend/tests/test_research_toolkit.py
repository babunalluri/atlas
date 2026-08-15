"""Payoff and snapshot math for the Stock Broker research toolkit."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[3] / "Instructions" / "StockBroker" / "tools"
sys.path.insert(0, str(TOOLS))

from research_toolkit import (  # noqa: E402
    analyze_option_payoff,
    compute_compare_symbols,
    compute_stock_snapshot,
    payoff_at_spot,
)


def test_long_call_payoff_breakeven_and_max_loss() -> None:
    result = analyze_option_payoff(
        structure="long_call",
        strike=100,
        premium=5,
        spots=[90, 100, 105, 110],
    )
    assert result["ok"] is True
    data = result["data"]
    assert data["max_loss_per_unit"] == 5
    assert data["max_profit"] is None
    assert data["unlimited_profit"] is True
    assert data["breakevens"] == [105]
    by_spot = {row["spot"]: row["pnl_per_unit"] for row in data["payoff_at_spots"]}
    assert by_spot[90] == -5
    assert by_spot[100] == -5
    assert by_spot[105] == 0
    assert by_spot[110] == 5


def test_bull_call_spread_payoff_math() -> None:
    result = analyze_option_payoff(
        structure="bull_call_spread",
        long_strike=100,
        long_premium=6,
        short_strike=110,
        short_premium=2,
        quantity=1,
        lot_size=50,
        spots=[100, 104, 110],
    )
    assert result["ok"] is True
    data = result["data"]
    assert data["max_loss_per_unit"] == 4
    assert data["max_profit_per_unit"] == 6
    assert data["max_loss"] == 200
    assert data["max_profit"] == 300
    assert data["breakevens"] == [104]
    by_spot = {row["spot"]: row["pnl"] for row in data["payoff_at_spots"]}
    assert by_spot[100] == -200
    assert by_spot[104] == 0
    assert by_spot[110] == 300


def test_iron_condor_credit_and_wings() -> None:
    result = analyze_option_payoff(
        structure="iron_condor",
        long_put_strike=90,
        long_put_premium=1,
        short_put_strike=95,
        short_put_premium=3,
        short_call_strike=105,
        short_call_premium=3,
        long_call_strike=110,
        long_call_premium=1,
        spots=[90, 91, 100, 109, 110],
    )
    assert result["ok"] is True
    data = result["data"]
    assert data["max_profit_per_unit"] == 4
    assert data["max_loss_per_unit"] == 1
    assert data["breakevens"] == [91, 109]
    assert payoff_at_spot(
        "iron_condor",
        {
            "long_put_strike": 90,
            "long_put_premium": 1,
            "short_put_strike": 95,
            "short_put_premium": 3,
            "short_call_strike": 105,
            "short_call_premium": 3,
            "long_call_strike": 110,
            "long_call_premium": 1,
        },
        100,
    ) == 4


def test_option_payoff_refuses_missing_premium() -> None:
    result = analyze_option_payoff(structure="long_put", strike=100, premium=0)
    assert result["ok"] is False
    assert "premium" in result["error"]
    assert "Do not invent" in result["error"]


def test_stock_snapshot_uses_supplied_prints_only() -> None:
    result = compute_stock_snapshot(
        symbol="RELIANCE",
        last_price=1400,
        open_price=1380,
        high=1410,
        low=1375,
        previous_close=1390,
        closes=[1360, 1370, 1380, 1390, 1400],
    )
    assert result["ok"] is True
    data = result["data"]
    assert data["last_price"] == 1400
    assert data["momentum"] == "up"
    assert data["intraday_candle"] == "bullish"
    assert data["sma5"] == 1380
    assert data["support"] == 1375
    assert data["resistance"] == 1410


def test_stock_snapshot_refuses_invented_quote() -> None:
    result = compute_stock_snapshot(symbol="TCS")
    assert result["ok"] is False
    assert "Do not invent" in result["error"]


def test_compare_symbols_from_supplied_ltps() -> None:
    result = compute_compare_symbols(
        symbol_a="TCS",
        ltp_a=4000,
        previous_close_a=3900,
        symbol_b="INFY",
        ltp_b=1500,
        previous_close_b=1520,
    )
    assert result["ok"] is True
    assert result["data"]["stronger_today"] == "TCS"


@pytest.mark.asyncio
async def test_named_tools_match_compute_helpers() -> None:
    from research_toolkit import research_option_payoff, research_stock_snapshot

    snapshot = await research_stock_snapshot(
        None, symbol="NIFTY", last_price=22000, open_price=21900
    )
    assert snapshot["ok"] is True
    payoff = await research_option_payoff(
        None, structure="covered_call", strike=110, premium=3, stock_entry=100
    )
    assert payoff["ok"] is True
    assert payoff["data"]["max_profit_per_unit"] == 13
    assert payoff["data"]["breakevens"] == [97]
