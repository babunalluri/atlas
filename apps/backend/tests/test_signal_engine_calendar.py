"""Calendar and chain heuristic tests (batch 2)."""

from __future__ import annotations

from datetime import date

from app.domains.signal_engine_calendar import (
    FOMC_MEETING_DATES,
    calendar_fields_from_nse,
    days_until_next_fomc,
    europe_session_max_abs_chg,
    macro_events_next_days,
    mock_calendar_fields,
)
from app.domains.signal_engine_chain import writer_grip_score


def test_calendar_fields_from_nse() -> None:
    holidays = {
        "data": [{"tradingDate": "2026-08-20", "description": "Holiday"}],
    }
    corp = {"data": [{"date": "2026-08-20"}, {"date": "2026-08-21"}]}
    out = calendar_fields_from_nse(
        holiday_body=holidays,
        corp_body=corp,
        ref=date(2026, 8, 20),
    )
    assert out["nse_holiday_today"] == 1.0
    assert out["market_holiday_any"] == 1.0
    assert out["nse_corp_events_today"] == 1.0
    assert "fed_meeting_proximity_days" in out


def test_calendar_parses_nse_dd_mmm_yyyy_and_segment_keys() -> None:
    holidays = {
        "CM": [{"tradingDate": "26-Jan-2026", "description": "Republic Day"}],
        "FO": [{"tradingDate": "26-Jan-2026", "description": "Republic Day"}],
    }
    corp = {"data": [{"exDate": "26-Jan-2026"}, {"exDate": "27-Jan-2026"}]}
    out = calendar_fields_from_nse(
        holiday_body=holidays,
        corp_body=corp,
        ref=date(2026, 1, 26),
    )
    assert out["nse_holiday_today"] == 1.0
    assert out["nse_corp_events_today"] == 1.0


def test_calendar_parses_nse_dd_full_month_yyyy() -> None:
    holidays = {"data": [{"tradingDate": "26-January-2025"}]}
    out = calendar_fields_from_nse(
        holiday_body=holidays,
        corp_body=None,
        ref=date(2025, 1, 26),
    )
    assert out["nse_holiday_today"] == 1.0


def test_calendar_parses_nse_dd_mmmm_yyyy_with_time_suffix() -> None:
    holidays = {"data": [{"tradingDate": "26-January-2025 00:00:00"}]}
    out = calendar_fields_from_nse(
        holiday_body=holidays,
        corp_body=None,
        ref=date(2025, 1, 26),
    )
    assert out["nse_holiday_today"] == 1.0


def test_europe_session_max_abs_chg() -> None:
    val = europe_session_max_abs_chg(
        {"global_ftse_chg": 0.2, "global_cac40_chg": -0.45, "global_dax_chg": 0.1}
    )
    assert val == 0.45


def test_macro_events_next_days() -> None:
    assert macro_events_next_days(date(2026, 1, 27), 7) >= 1.0


def test_writer_grip_score() -> None:
    strikes = [100, 150, 200]
    ce = {100: 10.0, 150: 500.0, 200: 20.0}
    pe = {100: 10.0, 150: 400.0, 200: 20.0}
    score = writer_grip_score(strikes, ce, pe)
    assert score is not None
    assert score > 0.5


def test_days_until_next_fomc_extends_past_2026() -> None:
    assert days_until_next_fomc(date(2027, 1, 4)) == 23.0


def test_mock_calendar_preserves_fomc_meeting_today_zero() -> None:
    out = mock_calendar_fields(ref=date(2026, 1, 28))
    assert out["fed_meeting_proximity_days"] == 0.0
    assert out["fed_meeting_today"] == 1.0


def test_fomc_2025_statement_dates_corrected() -> None:
    assert date(2025, 10, 29) in FOMC_MEETING_DATES
    assert date(2025, 12, 10) in FOMC_MEETING_DATES
