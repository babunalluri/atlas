"""Tests for Options Lab equity F&O underlying helpers."""

from __future__ import annotations

from app.domains.options_lab_underlyings import (
    infer_strike_step,
    parse_nfo_instruments_csv,
    suggest_equity_fut_symbol,
)


SAMPLE_CSV = """instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,instrument_type,segment,exchange
1,1,RELIANCE26AUGFUT,RELIANCE,0,2026-08-27,,0.05,250,FUT,NFO-FUT,NFO
2,2,RELIANCE26AUG2800CE,RELIANCE,0,2026-08-27,2800,0.05,250,CE,NFO-OPT,NFO
3,3,RELIANCE26AUG2820CE,RELIANCE,0,2026-08-27,2820,0.05,250,CE,NFO-OPT,NFO
4,4,RELIANCE26AUG2840CE,RELIANCE,0,2026-08-27,2840,0.05,250,CE,NFO-OPT,NFO
5,5,NIFTY26AUG24500CE,NIFTY,0,2026-08-27,24500,0.05,75,CE,NFO-OPT,NFO
6,6,INFY26AUG1500CE,INFY,0,2026-08-27,1500,0.05,400,CE,NFO-OPT,NFO
7,7,INFY26AUG1525CE,INFY,0,2026-08-27,1525,0.05,400,CE,NFO-OPT,NFO
"""


def test_infer_strike_step_mode() -> None:
    assert infer_strike_step([2800, 2820, 2840, 2860]) == 20
    assert infer_strike_step([1500, 1525, 1550]) == 25


def test_parse_nfo_instruments_skips_indices() -> None:
    rows = parse_nfo_instruments_csv(SAMPLE_CSV)
    symbols = {row["symbol"] for row in rows}
    assert "NSE:RELIANCE" in symbols
    assert "NSE:INFY" in symbols
    assert "NSE:NIFTY" not in symbols
    reliance = next(row for row in rows if row["symbol"] == "NSE:RELIANCE")
    assert reliance["strike_step"] == 20
    assert reliance["fut_symbol"].endswith("FUT")
    assert reliance["lot_size"] == 250


def test_extract_instruments_list_of_dicts() -> None:
    from app.domains.options_lab_underlyings import (
        extract_instruments_rows,
        parse_nfo_instrument_rows,
    )

    rows = [
        {
            "tradingsymbol": "RELIANCE26AUGFUT",
            "name": "RELIANCE",
            "expiry": "2026-08-27",
            "strike": 0,
            "lot_size": 250,
            "instrument_type": "FUT",
            "segment": "NFO-FUT",
            "exchange": "NFO",
        },
        {
            "tradingsymbol": "RELIANCE26AUG2800CE",
            "name": "RELIANCE",
            "expiry": "2026-08-27",
            "strike": 2800,
            "lot_size": 250,
            "instrument_type": "CE",
            "segment": "NFO-OPT",
            "exchange": "NFO",
        },
        {
            "tradingsymbol": "RELIANCE26AUG2820CE",
            "name": "RELIANCE",
            "expiry": "2026-08-27",
            "strike": 2820,
            "lot_size": 250,
            "instrument_type": "CE",
            "segment": "NFO-OPT",
            "exchange": "NFO",
        },
    ]
    assert extract_instruments_rows(rows) is not None
    assert extract_instruments_rows({"data": rows}) is not None
    presets = parse_nfo_instrument_rows(rows)
    assert any(p["symbol"] == "NSE:RELIANCE" and p["strike_step"] == 20 for p in presets)


def test_suggest_equity_fut_symbol() -> None:
    fut = suggest_equity_fut_symbol("NSE:RELIANCE")
    assert fut.startswith("NFO:RELIANCE")
    assert fut.endswith("FUT")
    assert suggest_equity_fut_symbol("NSE:NIFTY") == ""
