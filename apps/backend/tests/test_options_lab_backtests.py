"""Tests for Options Lab model backtests (Wave 1)."""

from __future__ import annotations

import pytest

from app.domains import signal_engine_cache as cache
from app.domains.options_lab_backtests import (
    create_backtest,
    delete_backtest,
    get_backtest,
    list_backtests,
    portfolio_summary,
    run_model_backtest,
    strategy_pnl_at_spot,
    summarize_backtests,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    cache.reset_signal_cache_for_tests()
    yield
    cache.reset_signal_cache_for_tests()


LEGS = [
    {"side": "buy", "type": "CE", "strike": 24500, "qty": 1, "premium": 100},
    {"side": "buy", "type": "PE", "strike": 24500, "qty": 1, "premium": 90},
]


def test_long_straddle_payoff_at_spot() -> None:
    pnl_atm = strategy_pnl_at_spot(LEGS, 24500)
    assert pnl_atm == pytest.approx(-190.0)
    pnl_up = strategy_pnl_at_spot(LEGS, 24800)
    assert pnl_up == pytest.approx((300 - 100) + (0 - 90))


def test_run_model_backtest_sqrt_t_grows_with_window() -> None:
    short = run_model_backtest(legs=LEGS, spot=24500, days=5, shock_pct=1.0, path_bias="up")
    long = run_model_backtest(legs=LEGS, spot=24500, days=20, shock_pct=1.0, path_bias="up")
    assert short is not None and long is not None
    assert short["shocks"][-1]["up"] < long["shocks"][-1]["up"]
    assert short["fidelity"] == "model"
    assert "hit_rate" in short["stats"]


def test_flat_path_is_not_constant() -> None:
    out = run_model_backtest(legs=LEGS, spot=24500, days=10, shock_pct=2.0, path_bias="flat")
    assert out is not None
    path_spots = {round(s["path_spot"], 4) for s in out["shocks"]}
    assert len(path_spots) > 1


@pytest.mark.asyncio
async def test_create_list_get_delete_backtest() -> None:
    created = await create_backtest(
        "tenant-bt",
        {
            "name": "Straddle probe",
            "legs": LEGS,
            "spot": 24500,
            "days": 8,
            "shock_pct": 1.5,
            "path_bias": "up",
            "underlying_symbol": "NSE:NIFTY 50",
        },
    )
    assert created["ok"] is True
    bt_id = created["backtest"]["id"]
    listed = await list_backtests("tenant-bt")
    assert listed["count"] == 1
    assert listed["backtests"][0]["stats"]["hit_rate"] is not None

    got = await get_backtest("tenant-bt", bt_id)
    assert got["ok"] is True
    assert len(got["backtest"]["result"]["shocks"]) == 8

    deleted = await delete_backtest("tenant-bt", bt_id)
    assert deleted["ok"] is True
    listed2 = await list_backtests("tenant-bt")
    assert listed2["count"] == 0


@pytest.mark.asyncio
async def test_portfolio_summary_averages_runs() -> None:
    a = await create_backtest(
        "tenant-sum",
        {"name": "A", "legs": LEGS, "spot": 24500, "days": 10, "path_bias": "up"},
    )
    b = await create_backtest(
        "tenant-sum",
        {"name": "B", "legs": LEGS, "spot": 24500, "days": 10, "path_bias": "down"},
    )
    assert a["ok"] and b["ok"]
    summary = await summarize_backtests("tenant-sum", limit=5)
    assert summary["ok"] is True
    assert summary["count"] == 2
    assert summary["stats"]["avg_hit_rate"] is not None
    assert any(c.get("corr") is not None for c in summary.get("correlations") or [])

    direct = portfolio_summary([a["backtest"], b["backtest"]])
    assert direct["count"] == 2
