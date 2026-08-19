"""Tests for levels, NSE slow tier, and option chain helpers."""

from __future__ import annotations

from app.domains.signal_engine_chain import (
    build_chain_symbols,
    chain_metrics_from_quotes,
    strike_ladder,
)
from app.domains.signal_engine_levels import levels_from_candles, mock_levels
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


def test_mock_helpers() -> None:
    assert mock_nse_fields()["fii_net"] == 850.0
    assert "pivot_point" in mock_levels(24300.0)


def test_parse_fii_dii() -> None:
    body = {"data": [{"category": "FII/FPI", "netValue": "1,234.56"}]}
    assert _parse_fii_dii(body) == 1234.56


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
