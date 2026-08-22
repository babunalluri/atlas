"""Tests for levels, NSE slow tier, and option chain helpers."""

from __future__ import annotations

from app.domains.signal_engine_chain import (
    build_chain_symbols,
    chain_metrics_from_quotes,
    strike_ladder,
)
from app.domains.signal_engine_levels import (
    chart_timeframe_snapshots,
    contextual_desk_chart_feeds,
    expiry_levels_from_daily,
    intraday_indicators_from_candles,
    levels_from_candles,
    mock_levels,
)
from app.domains.signal_engine_nse import _parse_advance_decline, _parse_fii_dii, mock_nse_fields


def test_strike_ladder() -> None:
    assert strike_ladder(24300, 50, wings=2) == [24200, 24250, 24300, 24350, 24400]


def test_build_chain_symbols() -> None:
    strikes, ce, pe = build_chain_symbols("NFO:NIFTY26AUGFUT", 24300, 50, wings=1)
    assert strikes == [24250, 24300, 24350]
    assert ce == ["NFO:NIFTY26AUG24250CE", "NFO:NIFTY26AUG24300CE", "NFO:NIFTY26AUG24350CE"]
    assert pe == ["NFO:NIFTY26AUG24250PE", "NFO:NIFTY26AUG24300PE", "NFO:NIFTY26AUG24350PE"]


def test_chain_metrics_pcr_and_max_pain() -> None:
    strikes = [24250, 24300, 24350]
    ce_syms = ["CE1", "CE2", "CE3"]
    pe_syms = ["PE1", "PE2", "PE3"]
    quotes = {
        "CE1": {"open_interest": 100},
        "CE2": {"open_interest": 200},
        "CE3": {"open_interest": 100},
        "PE1": {"open_interest": 150},
        "PE2": {"open_interest": 300},
        "PE3": {"open_interest": 150},
    }

    def find_row(q: dict, sym: str):
        return q.get(sym)

    out = chain_metrics_from_quotes(
        quotes,
        find_row=find_row,
        strikes=strikes,
        ce_symbols=ce_syms,
        pe_symbols=pe_syms,
    )
    assert out["pcr"] == 1.5
    assert out["max_pain"] == 24300.0
    assert out["chain_ce_oi"] == 400
    assert out["chain_pe_oi"] == 600
    assert "writer_grip_score" in out


def test_levels_from_candles() -> None:
    daily = [
        ["2026-08-18", 100, 120, 95, 110],
        ["2026-08-19", 110, 125, 108, 122],
    ]
    intra = [
        ["2026-08-19 09:15", 110, 115, 109, 114],
        ["2026-08-19 09:20", 114, 118, 113, 117],
    ]
    out = levels_from_candles(daily_candles=daily, intraday_5m=intra, spot=112.0)
    assert out["prev_day_high"] == 120
    assert out["prev_day_low"] == 95
    assert out["first_5m_high"] == 115
    assert out["first_5m_low"] == 109
    assert out["day_high"] == 125
    assert out["day_low"] == 108
    assert out["inside_first_5m_range"] == 1.0
    assert "spot_vs_sma20_5m" not in out  # only 2 candles — SMA20 not computed


def test_chart_timeframe_snapshots() -> None:
    daily = [
        ["2026-07-01", 100, 105, 98, 102],
        ["2026-07-02", 102, 108, 101, 106],
        ["2026-07-03", 106, 110, 104, 108],
        ["2026-07-04", 108, 112, 107, 111],
        ["2026-07-07", 111, 115, 110, 114],
        ["2026-07-08", 114, 118, 113, 117],
    ]
    minute = [
        ["2026-08-19 09:15", 110, 111, 109, 110.5],
        ["2026-08-19 09:16", 110.5, 112, 110, 111.2],
    ]
    five_min = [
        ["2026-08-19 09:15", 110, 115, 109, 114],
        ["2026-08-19 09:20", 114, 118, 113, 116],
    ]
    hour = [
        ["2026-08-19 09:00", 108, 112, 107, 110],
        ["2026-08-19 10:00", 110, 114, 109, 113],
    ]
    out = chart_timeframe_snapshots(
        minute_candles=minute,
        five_min_candles=five_min,
        hour_candles=hour,
        daily_candles=daily,
    )
    assert out["chart_1m_bar_chg_pct"] == round((111.2 - 110.5) / 110.5 * 100, 3)
    assert out["chart_5m_bar_chg_pct"] == round((116 - 114) / 114 * 100, 3)
    assert out["chart_60m_bar_chg_pct"] == round((113 - 110) / 110 * 100, 3)
    assert out["chart_1d_bar_chg_pct"] == round((117 - 114) / 114 * 100, 3)
    assert "chart_1w_bar_chg_pct" in out
    assert "chart_1mo_bar_chg_pct" not in out  # need 23 daily bars for 22-session lookback


def test_contextual_desk_chart_feeds() -> None:
    base = {
        "nifty_points_move": 55.0,
        "chart_1m_bar_chg_pct": 0.12,
        "chart_5m_bar_chg_pct": -0.08,
        "ist_hour": 15.1,
    }
    out = contextual_desk_chart_feeds(base)
    assert out["chart_1m_post_big_move_pct"] == 0.12
    assert out["chart_5m_3pm_window_pct"] == -0.08

    quiet = contextual_desk_chart_feeds({**base, "nifty_points_move": 20.0})
    assert "chart_1m_post_big_move_pct" not in quiet

    early = contextual_desk_chart_feeds({**base, "ist_hour": 10.0})
    assert "chart_5m_3pm_window_pct" not in early


def test_mock_helpers() -> None:
    assert mock_nse_fields()["fii_net"] == 850.0
    assert "pivot_point" in mock_levels(24300.0)


def test_parse_fii_dii() -> None:
    body = {"data": [{"category": "FII/FPI", "netValue": "1,234.56"}]}
    assert _parse_fii_dii(body) == 1234.56


def test_parse_fii_dii_nets_includes_dii() -> None:
    from app.domains.signal_engine_nse import _parse_fii_dii_nets

    body = {
        "data": [
            {"category": "FII/FPI", "netValue": "1,234.56"},
            {"category": "DII", "netValue": "-120.5"},
        ]
    }
    nets = _parse_fii_dii_nets(body)
    assert nets["fii_net"] == 1234.56
    assert nets["dii_net"] == -120.5


def test_mock_nse_fields_include_dii() -> None:
    fields = mock_nse_fields()
    assert fields["fii_net"] == 850.0
    assert "dii_net" in fields
    assert fields["dii_net"] == -120.0


def test_parse_advance_decline() -> None:
    body = [
        {"percentChange": 1.2},
        {"percentChange": -0.5},
        {"percentChange": 0.0},
    ]
    assert _parse_advance_decline(body) == 1.0


def test_parse_advance_decline_wrapped() -> None:
    body = {"data": [{"percentChange": 2.0}, {"percentChange": -1.0}]}
    assert _parse_advance_decline(body) == 1.0


def test_normalize_checklist_tickers() -> None:
    from app.domains.trade_desk_checklist import normalize_checklist_text, normalize_metrics

    assert normalize_checklist_text("Sbi big move") == "SBI big move"
    assert normalize_checklist_text("Adx - Nifty 1 minute,5 minutes") == "ADX — NIFTY 1 minute,5 minutes"
    assert normalize_checklist_text("Nifty ATM iv chart") == "NIFTY ATM IV chart"
    rows = normalize_metrics([{"id": "x", "label": "Pcr", "hint": "Pcr"}])
    assert rows[0]["label"] == "PCR"
    from app.domains.signal_engine_levels import apply_spot_derived_fields

    cached = {
        "first_5m_high": 115.0,
        "first_5m_low": 109.0,
        "inside_first_5m_range": 1.0,
    }
    apply_spot_derived_fields(cached, 116.0)
    assert cached["inside_first_5m_range"] == 0.0


def test_expiry_levels_from_daily() -> None:
    from datetime import date

    daily = [
        ["2026-07-30", 100, 110, 95, 105],
        ["2026-07-31", 105, 112, 103, 108],
        ["2026-08-01", 108, 118, 107, 115],
        ["2026-08-13", 112, 120, 111, 118],
        ["2026-08-20", 122, 128, 120, 126],
    ]
    out = expiry_levels_from_daily(daily, ref=date(2026, 8, 20))
    assert out["running_month_high"] == 128
    assert out["running_month_low"] == 107
    assert out["last_expiry_high"] == 120
    assert out["prev_month_expiry_high"] == 110


def test_intraday_indicators_from_candles() -> None:
    intra = [
        ["2026-08-20 09:15", 100, 101, 99, 100.5, 1000],
        ["2026-08-20 09:16", 100.5, 102, 100, 101.5, 1500],
    ]
    out = intraday_indicators_from_candles(intra, spot=101.5)
    assert out["vwap_1m"] == 100.77
    assert out["vwap_distance_pct"] == 0.728


def test_crypto_max_abs_change() -> None:
    from app.domains.signal_engine_yahoo import crypto_max_abs_change

    assert crypto_max_abs_change({"global_bitcoin_chg": 1.2, "global_eth_chg": -2.4}) == 2.4
