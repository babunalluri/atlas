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
    run_historical_close_backtest,
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


def test_run_historical_close_backtest_fidelity() -> None:
    closes = [24500.0, 24550.0, 24480.0, 24600.0, 24520.0]
    out = run_historical_close_backtest(legs=LEGS, closes=closes, shock_pct=2.0)
    assert out is not None
    assert out["fidelity"] == "model_hist"
    assert out["path_bias"] == "historical"
    assert out["days"] == 5
    assert out["shocks"][0]["path_spot"] == 24500.0
    assert out["shocks"][-1]["path_spot"] == 24520.0


@pytest.mark.asyncio
async def test_create_backtest_with_closes_is_model_hist() -> None:
    created = await create_backtest(
        "tenant-hist",
        {
            "name": "Hist path",
            "legs": LEGS,
            "spot": 24500,
            "days": 5,
            "closes": [24500, 24540, 24490, 24610, 24530],
            "use_historical": True,
            "underlying_symbol": "NSE:NIFTY 50",
        },
    )
    assert created["ok"] is True
    assert created["backtest"]["fidelity"] == "model_hist"
    assert created["backtest"]["result"]["path_bias"] == "historical"


def test_run_bs_mark_backtest_fidelity() -> None:
    from app.domains.options_lab_backtests import run_bs_mark_backtest

    out = run_bs_mark_backtest(
        legs=LEGS,
        spot=24500,
        days=8,
        shock_pct=2.0,
        path_bias="flat",
        iv_pct=18.0,
        entry_dte=10,
    )
    assert out is not None
    assert out["fidelity"] == "bs_marks"
    assert out["days"] == 8
    assert "iv_pct" in out
    # Flat-ish spot + theta: path equity should not be identical every day
    path_vals = {round(s["pnl_path"], 2) for s in out["shocks"]}
    assert len(path_vals) > 1


def test_bs_mark_short_dte_decays_toward_intrinsic() -> None:
    """Short DTE must retain theta — no 0.02y (~7d) floor flattening the path."""
    from app.domains.options_lab_backtests import run_bs_mark_backtest

    out = run_bs_mark_backtest(
        legs=LEGS,
        spot=24500,
        days=5,
        shock_pct=0.5,
        path_bias="flat",
        iv_pct=15.0,
        entry_dte=5,
    )
    assert out is not None
    assert out["days"] == 5
    first = out["shocks"][0]["pnl_path"]
    last = out["shocks"][-1]["pnl_path"]
    intrinsic = strategy_pnl_at_spot(LEGS, out["shocks"][-1]["path_spot"])
    # Path should move (theta), and day-5 mark should be near expiry intrinsic.
    assert first != pytest.approx(last, abs=1.0)
    assert last == pytest.approx(intrinsic, abs=0.01)


def test_bs_mark_path_clamped_to_entry_dte() -> None:
    from app.domains.options_lab_backtests import run_bs_mark_backtest

    out = run_bs_mark_backtest(
        legs=LEGS,
        spot=24500,
        days=20,
        shock_pct=1.0,
        path_bias="flat",
        iv_pct=18.0,
        entry_dte=5,
    )
    assert out is not None
    assert out["days"] == 5


@pytest.mark.asyncio
async def test_create_backtest_with_marks_is_bs_marks() -> None:
    created = await create_backtest(
        "tenant-bs",
        {
            "name": "BS marks",
            "legs": LEGS,
            "spot": 24500,
            "days": 6,
            "use_marks": True,
            "iv_pct": 16,
            "entry_dte": 8,
        },
    )
    assert created["ok"] is True
    assert created["backtest"]["fidelity"] == "bs_marks"
