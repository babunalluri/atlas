"""Tests for admin Options Lab chain snapshots."""

from __future__ import annotations

import pytest

from app.domains import signal_engine_cache as cache
from app.domains.options_lab import (
    OptionsLabConfig,
    _build_rows,
    _pct_change,
    append_straddle_point,
    apply_screener_session_deltas,
    build_oi_chart_rows,
    compose_screener_row,
    _downsample_straddle_points,
    ensure_oi_baseline,
    ensure_screener_baselines,
    mock_chain_snapshot,
    mock_screener_rows,
    screener_presets,
    suggest_fut_symbol,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    cache.reset_signal_cache_for_tests()
    yield
    cache.reset_signal_cache_for_tests()


def test_mock_chain_snapshot_has_rows_and_summary() -> None:
    config = OptionsLabConfig(
        underlying_symbol="NSE:NIFTY 50",
        fut_symbol="NFO:NIFTY26AUGFUT",
        strike_step=50,
        mock=True,
    )
    out = mock_chain_snapshot(config, wings=5)
    assert out["ok"] is True
    assert out["mock"] is True
    assert len(out["rows"]) == 11
    assert out["summary"]["pcr"] is not None
    assert out["summary"]["max_pain"] is not None
    assert any(row["is_atm"] for row in out["rows"])


def test_options_lab_config_fingerprint_changes_with_setup() -> None:
    base = OptionsLabConfig(
        underlying_symbol="NSE:NIFTY 50",
        fut_symbol="NFO:NIFTY26AUGFUT",
        strike_step=50,
    )
    other = OptionsLabConfig(
        underlying_symbol="NSE:BANKNIFTY",
        fut_symbol="NFO:BANKNIFTY26AUGFUT",
        strike_step=100,
    )
    assert base.cache_fingerprint() != other.cache_fingerprint()


def test_build_rows_marks_atm() -> None:
    quotes = {
        "NFO:NIFTY24200CE": {"last_price": 180, "open_interest": 1000, "volume": 50},
        "NFO:NIFTY24200PE": {"last_price": 40, "open_interest": 900, "volume": 45},
        "NFO:NIFTY24300CE": {"last_price": 120, "open_interest": 2000, "volume": 80},
        "NFO:NIFTY24300PE": {"last_price": 55, "open_interest": 1800, "volume": 70},
    }
    rows = _build_rows(
        strikes=[24200, 24300],
        ce_symbols=["NFO:NIFTY24200CE", "NFO:NIFTY24300CE"],
        pe_symbols=["NFO:NIFTY24200PE", "NFO:NIFTY24300PE"],
        quotes=quotes,
        atm=24300,
    )
    assert rows[1]["is_atm"] is True
    assert rows[1]["ce"]["ltp"] == 120
    assert rows[1]["pe"]["oi"] == 1800


def test_build_oi_chart_rows_computes_change_from_baseline() -> None:
    rows = [
        {
            "strike": 24300,
            "is_atm": True,
            "ce": {"oi": 1200.0},
            "pe": {"oi": 900.0},
        }
    ]
    baseline = {"24300": {"ce_oi": 1000.0, "pe_oi": 950.0}}
    out = build_oi_chart_rows(rows, baseline)
    assert out[0]["ce_oi_chg"] == 200.0
    assert out[0]["pe_oi_chg"] == -50.0


@pytest.mark.asyncio
async def test_straddle_history_appends_and_trims() -> None:
    tenant = "tenant-test"
    rows = [
        {
            "strike": 24300,
            "is_atm": True,
            "ce": {"ltp": 120.0},
            "pe": {"ltp": 55.0},
        }
    ]
    fingerprint = "fp1"
    hist = await append_straddle_point(
        tenant,
        fingerprint,
        rows,
        fetched_at=100,
        atm=24300,
    )
    points = hist["points"]
    assert len(points) == 1
    assert points[0]["combined"] == 175.0
    assert "24300" in hist["series"]

    hist = await append_straddle_point(
        tenant,
        fingerprint,
        rows,
        fetched_at=100,
        atm=24300,
    )
    points = hist["points"]
    assert len(points) == 1

    hist = await append_straddle_point(
        tenant,
        fingerprint,
        rows,
        fetched_at=101,
        atm=24300,
    )
    points = hist["points"]
    assert len(points) == 2


@pytest.mark.asyncio
async def test_straddle_history_resets_on_new_trading_day(monkeypatch) -> None:
    from app.domains import options_lab as ol

    rows = [
        {
            "strike": 24300,
            "is_atm": True,
            "ce": {"ltp": 120.0},
            "pe": {"ltp": 55.0},
        }
    ]
    tenant = "tenant-test"
    monkeypatch.setattr(ol, "_ist_trading_day", lambda: "2026-08-20")
    await append_straddle_point(tenant, "fp1", rows, fetched_at=100, atm=24300)

    monkeypatch.setattr(ol, "_ist_trading_day", lambda: "2026-08-21")
    hist = await append_straddle_point(tenant, "fp1", rows, fetched_at=200, atm=24300)
    points = hist["points"]
    assert len(points) == 1
    assert points[0]["t"] == 200


@pytest.mark.asyncio
async def test_ensure_oi_baseline_merges_new_strikes_when_wings_widen() -> None:
    tenant = "tenant-test"
    narrow = [
        {
            "strike": 24300,
            "is_atm": True,
            "ce": {"oi": 1000.0},
            "pe": {"oi": 900.0},
        }
    ]
    await ensure_oi_baseline(tenant, "fp1", narrow)
    wide = [
        *narrow,
        {
            "strike": 24350,
            "is_atm": False,
            "ce": {"oi": 800.0},
            "pe": {"oi": 700.0},
        },
    ]
    baseline = await ensure_oi_baseline(tenant, "fp1", wide)
    assert "24350" in baseline["strikes"]
    out = build_oi_chart_rows(wide, baseline["strikes"])
    by_strike = {row["strike"]: row for row in out}
    assert by_strike[24350]["ce_oi_chg"] == 0.0
    assert by_strike[24300]["ce_oi_chg"] == 0.0


@pytest.mark.asyncio
async def test_ensure_oi_baseline_force_resets() -> None:
    tenant = "tenant-test"
    rows = [
        {
            "strike": 24300,
            "is_atm": True,
            "ce": {"oi": 1000.0},
            "pe": {"oi": 900.0},
        }
    ]
    first = await ensure_oi_baseline(tenant, "fp1", rows)
    second = await ensure_oi_baseline(tenant, "fp1", rows)
    assert first["set_at"] == second["set_at"]

    rows[0]["ce"]["oi"] = 1500.0
    third = await ensure_oi_baseline(tenant, "fp1", rows, force=True)
    assert third["strikes"]["24300"]["ce_oi"] == 1500.0
    assert first["strikes"]["24300"]["ce_oi"] == 1000.0


def test_suggest_fut_symbol_for_nifty() -> None:
    out = suggest_fut_symbol("NSE:NIFTY 50")
    assert out.startswith("NFO:NIFTY")
    assert out.endswith("FUT")


def test_suggest_fut_symbol_rolls_after_monthly_expiry() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    ist = ZoneInfo("Asia/Kolkata")
    on_expiry = datetime(2026, 8, 27, 15, 30, tzinfo=ist)
    after_expiry = datetime(2026, 8, 28, 9, 15, tzinfo=ist)
    assert suggest_fut_symbol("NSE:NIFTY 50", on_expiry) == "NFO:NIFTY26AUGFUT"
    assert suggest_fut_symbol("NSE:NIFTY 50", after_expiry) == "NFO:NIFTY26SEPFUT"


def test_screener_presets_equities_and_all() -> None:
    equities = screener_presets("equities")
    assert any(row["symbol"] == "NSE:RELIANCE" for row in equities)
    assert not any("NIFTY" in row["symbol"] for row in equities)
    all_rows = screener_presets("all")
    symbols = [row["symbol"] for row in all_rows]
    assert "NSE:NIFTY 50" in symbols
    assert "NSE:RELIANCE" in symbols


def test_suggest_fut_symbol_equity() -> None:
    fut = suggest_fut_symbol("NSE:RELIANCE")
    assert fut.startswith("NFO:RELIANCE") and fut.endswith("FUT")


def test_fut_matches_underlying() -> None:
    from app.domains.options_lab import _fut_matches_underlying

    assert _fut_matches_underlying("NFO:NIFTY26AUGFUT", "NSE:NIFTY 50")
    assert not _fut_matches_underlying("NFO:NIFTY26AUGFUT", "NSE:RELIANCE")
    assert _fut_matches_underlying("NFO:RELIANCE26AUGFUT", "NSE:RELIANCE")


def test_mock_screener_rows_cover_all_presets() -> None:
    presets = screener_presets("indices")
    rows = mock_screener_rows(presets)
    assert len(rows) == len(presets)
    assert rows[0]["pcr"] is not None
    assert rows[0]["atm_iv"] is not None


def test_pct_change_handles_zero_baseline() -> None:
    assert _pct_change(110.0, 100.0) == 10.0
    assert _pct_change(110.0, 0.0) is None


def test_compose_screener_row_defers_deltas_to_session_apply() -> None:
    row = compose_screener_row(
        {"symbol": "NSE:NIFTY 50", "label": "Nifty"},
        spot=24300.0,
        atm=24300,
        fut_symbol="NFO:NIFTYFUT",
        summary={"chain_ce_oi": 1000.0, "chain_pe_oi": 900.0},
        rows=[],
    )
    assert row["oi_pct_chg"] is None
    assert row["iv_chg"] is None


@pytest.mark.asyncio
async def test_ensure_screener_baselines_sets_session_start() -> None:
    tenant = "tenant-test"
    baselines = await ensure_screener_baselines(
        tenant,
        {
            "NSE:NIFTY 50": {
                "atm_iv": 12.0,
                "chain_ce_oi": 1000.0,
                "chain_pe_oi": 900.0,
                "fut_oi": 500.0,
            }
        },
    )
    assert baselines["NSE:NIFTY 50"]["atm_iv"] == 12.0
    same = await ensure_screener_baselines(
        tenant,
        {
            "NSE:NIFTY 50": {
                "atm_iv": 13.0,
                "chain_ce_oi": 1100.0,
                "chain_pe_oi": 950.0,
                "fut_oi": 520.0,
            }
        },
    )
    assert same["NSE:NIFTY 50"]["atm_iv"] == 12.0


def test_apply_screener_session_deltas() -> None:
    baselines = {
        "NSE:NIFTY 50": {
            "atm_iv": 10.0,
            "chain_ce_oi": 1000.0,
            "chain_pe_oi": 900.0,
        }
    }
    rows = [
        {
            "underlying_symbol": "NSE:NIFTY 50",
            "underlying_label": "NIFTY 50",
            "error": None,
            "atm_iv": 11.0,
            "chain_ce_oi": 1100.0,
            "chain_pe_oi": 950.0,
        }
    ]
    out = apply_screener_session_deltas(rows, baselines)
    assert out[0]["iv_chg"] == 10.0
    assert out[0]["oi_pct_chg"] == 7.89


def test_downsample_straddle_points_caps_payload() -> None:
    points = [{"t": i, "ce": 1.0, "pe": 1.0, "combined": 2.0, "atm": 24300} for i in range(2_000)]
    out = _downsample_straddle_points(points, limit=600)
    assert len(out) == 600
    assert out[0]["t"] == 0
    assert out[-1]["t"] == 1999


@pytest.mark.asyncio
async def test_append_flows_day_upserts_and_caps() -> None:
    from app.domains.options_lab import FLOWS_HISTORY_MAX_DAYS, append_flows_day

    series = await append_flows_day("tenant-flows", fii_net=100.0, dii_net=-50.0)
    assert len(series) >= 1
    assert series[-1]["label"] == "Today"
    assert series[-1]["fii_net"] == 100.0

    again = await append_flows_day("tenant-flows", fii_net=120.0, dii_net=-40.0)
    today_rows = [r for r in again if r["label"] == "Today"]
    assert len(today_rows) == 1
    assert today_rows[0]["fii_net"] == 120.0

    mock_series = await append_flows_day(
        "tenant-flows-mock",
        fii_net=850.0,
        dii_net=-120.0,
        mock_seed=True,
    )
    assert len(mock_series) >= 5
    assert len(mock_series) <= FLOWS_HISTORY_MAX_DAYS


@pytest.mark.asyncio
async def test_append_flows_day_preserves_partial_side() -> None:
    from app.domains.options_lab import append_flows_day

    await append_flows_day("tenant-partial", fii_net=100.0, dii_net=-50.0)
    again = await append_flows_day("tenant-partial", fii_net=None, dii_net=-10.0)
    today = [r for r in again if r["label"] == "Today"][0]
    assert today["fii_net"] == 100.0
    assert today["dii_net"] == -10.0


def test_leg_from_quote_does_not_use_ohlc_as_oi_or_iv() -> None:
    from app.domains.options_lab import _leg_from_quote

    leg = _leg_from_quote(
        {"ohlc": {"close": 130.0}, "last_price": 12.5},
        symbol="NFO:NIFTY26AUG24500CE",
    )
    assert leg["ltp"] == 12.5
    assert leg["oi"] is None
    assert leg["volume"] is None
    assert leg["iv"] is None
    assert leg["delta"] is None


@pytest.mark.asyncio
async def test_signal_chain_seed_uses_row_atm_without_underlying_ltp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.domains.options_lab import OptionsLabConfig, OptionsLabService

    now_ms = int(time.time() * 1000)
    snap = {
        "instrument": "NSE:NIFTY 50",
        "underlying": {"symbol": "NSE:NIFTY 50", "label": "NIFTY 50"},
        "atm": 24500,
        "ce_symbol": "NFO:NIFTY26AUG24500CE",
        "pe_symbol": "NFO:NIFTY26AUG24500PE",
        "computed_at_ms": now_ms,
        "data_age_ms": 0,
        "snapshot_stale": False,
    }

    async def fake_merged_frame(
        tenant_id: str,
        *,
        instrument: str | None = None,
    ) -> dict:
        assert instrument == "NSE:NIFTY 50"
        return snap

    monkeypatch.setattr(
        "app.domains.signal_engine_cache.merged_frame",
        fake_merged_frame,
    )

    session = MagicMock()
    session.info = {"tenant_id": "tenant-seed"}
    svc = OptionsLabService(
        session=session,
        context=SimpleNamespace(tenant_id="tenant-seed"),
    )
    config = OptionsLabConfig(
        underlying_symbol="NSE:NIFTY 50",
        fut_symbol="NFO:NIFTY26AUGFUT",
        strike_step=50,
        mock=False,
    )
    seed = await svc._signal_chain_seed(config)
    assert seed is not None
    assert seed["atm"] == 24500
    assert seed["spot"] == 24500.0
    assert seed["quote_source"] == "signal_board"


@pytest.mark.asyncio
async def test_signal_chain_seed_rejects_stale_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.domains.options_lab import OptionsLabConfig, OptionsLabService

    snap = {
        "instrument": "NSE:NIFTY 50",
        "underlying": {"symbol": "NSE:NIFTY 50", "label": "NIFTY 50"},
        "atm": 24500,
        "nifty_ltp": 24487.5,
        "snapshot_stale": True,
    }

    async def fake_merged_frame(
        tenant_id: str,
        *,
        instrument: str | None = None,
    ) -> dict:
        return snap

    monkeypatch.setattr(
        "app.domains.signal_engine_cache.merged_frame",
        fake_merged_frame,
    )

    session = MagicMock()
    session.info = {"tenant_id": "tenant-seed"}
    svc = OptionsLabService(
        session=session,
        context=SimpleNamespace(tenant_id="tenant-seed"),
    )
    config = OptionsLabConfig(
        underlying_symbol="NSE:NIFTY 50",
        fut_symbol="NFO:NIFTY26AUGFUT",
        strike_step=50,
        mock=False,
    )
    assert await svc._signal_chain_seed(config) is None


def test_signal_frame_fresh_ignores_misleading_data_age_ms() -> None:
    import time

    from app.domains.options_lab import _signal_frame_fresh_for_seed

    old_ms = int(time.time() * 1000) - 600_000
    assert _signal_frame_fresh_for_seed(
        {"data_age_ms": 0, "computed_at_ms": old_ms, "snapshot_stale": False}
    ) is False
