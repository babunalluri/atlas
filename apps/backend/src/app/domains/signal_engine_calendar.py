"""Holiday, macro calendar, and corporate-event helpers (slow tier)."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# FOMC statement days (UTC calendar dates — desk uses as macro risk flags).
FOMC_MEETING_DATES: frozenset[date] = frozenset(
    {
        date(2025, 1, 29),
        date(2025, 3, 19),
        date(2025, 5, 7),
        date(2025, 6, 18),
        date(2025, 7, 30),
        date(2025, 9, 17),
        date(2025, 10, 29),
        date(2025, 12, 10),
        date(2026, 1, 28),
        date(2026, 3, 18),
        date(2026, 5, 6),
        date(2026, 6, 17),
        date(2026, 7, 29),
        date(2026, 9, 16),
        date(2026, 11, 5),
        date(2026, 12, 16),
        date(2027, 1, 27),
        date(2027, 3, 17),
        date(2027, 5, 5),
        date(2027, 6, 16),
        date(2027, 7, 28),
        date(2027, 9, 15),
        date(2027, 11, 3),
        date(2027, 12, 15),
    }
)

# NYSE full-day closures (extend yearly).
US_MARKET_HOLIDAYS: frozenset[date] = frozenset(
    {
        date(2025, 1, 1),
        date(2025, 1, 20),
        date(2025, 2, 17),
        date(2025, 4, 18),
        date(2025, 5, 26),
        date(2025, 6, 19),
        date(2025, 7, 4),
        date(2025, 9, 1),
        date(2025, 11, 27),
        date(2025, 12, 25),
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
        date(2027, 1, 1),
        date(2027, 1, 18),
        date(2027, 2, 15),
        date(2027, 4, 2),
        date(2027, 5, 31),
        date(2027, 6, 18),
        date(2027, 7, 5),
        date(2027, 9, 6),
        date(2027, 11, 25),
        date(2027, 12, 24),
    }
)

UK_MARKET_HOLIDAYS: frozenset[date] = frozenset(
    {
        date(2025, 1, 1),
        date(2025, 4, 18),
        date(2025, 4, 21),
        date(2025, 5, 5),
        date(2025, 5, 26),
        date(2025, 12, 25),
        date(2025, 12, 26),
        date(2026, 1, 1),
        date(2026, 4, 3),
        date(2026, 4, 6),
        date(2026, 5, 4),
        date(2026, 5, 25),
        date(2026, 12, 25),
        date(2026, 12, 28),
        date(2027, 1, 1),
        date(2027, 4, 2),
        date(2027, 4, 5),
        date(2027, 5, 3),
        date(2027, 5, 31),
        date(2027, 12, 27),
        date(2027, 12, 28),
    }
)


def _parse_nse_date(text: str) -> date | None:
    cleaned = text.strip()
    if not cleaned:
        return None
    date_part = cleaned.split()[0]
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            try:
                return datetime.strptime(date_part, fmt).date()
            except ValueError:
                continue
    try:
        return date.fromisoformat(cleaned[:10])
    except ValueError:
        return None


def _iter_nse_holiday_rows(body: Any) -> list[Any]:
    rows: list[Any] = []
    if isinstance(body, list):
        return [row for row in body if row is not None]
    if not isinstance(body, dict):
        return rows
    for key, val in body.items():
        if not isinstance(val, list):
            continue
        if key.lower() in {"data", "cbm", "cbms", "trading"} or key.isupper():
            rows.extend(row for row in val if row is not None)
    return rows


def parse_nse_holiday_dates(body: Any) -> set[date]:
    """Parse NSE holiday-master (or similar) JSON into calendar dates."""
    out: set[date] = set()
    for row in _iter_nse_holiday_rows(body):
        if not isinstance(row, dict):
            continue
        raw = row.get("tradingDate") or row.get("date") or row.get("holidayDate")
        if raw is None:
            continue
        parsed = _parse_nse_date(str(raw))
        if parsed is not None:
            out.add(parsed)
    return out


def _parse_nse_holiday_dates(body: Any) -> set[date]:
    return parse_nse_holiday_dates(body)


def _parse_nse_corp_events(body: Any, *, ref: date) -> int:
    rows: list[Any] = []
    if isinstance(body, dict) and isinstance(body.get("data"), list):
        rows = body["data"]
    elif isinstance(body, list):
        rows = body
    count = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = row.get("date") or row.get("exDate") or row.get("broadcastDate")
        if raw is None:
            continue
        parsed = _parse_nse_date(str(raw))
        if parsed == ref:
            count += 1
    return count


def days_until_next_fomc(ref: date) -> float | None:
    future = sorted(d for d in FOMC_MEETING_DATES if d >= ref)
    if not future:
        return None
    return float((future[0] - ref).days)


def macro_events_next_days(ref: date, days: int = 7) -> float:
    end = ref + timedelta(days=days)
    count = 0
    for d in FOMC_MEETING_DATES:
        if ref <= d <= end:
            count += 1
    return float(count)


def calendar_fields_from_nse(
    *,
    holiday_body: Any | None,
    corp_body: Any | None,
    ref: date | None = None,
) -> dict[str, float]:
    """Build checklist calendar feeds from NSE JSON + static macro calendars."""
    ref_day = ref or date.today()
    out: dict[str, float] = {}

    nse_holidays = _parse_nse_holiday_dates(holiday_body) if holiday_body else set()
    nse_holiday = 1.0 if ref_day in nse_holidays else 0.0
    us_holiday = 1.0 if ref_day in US_MARKET_HOLIDAYS else 0.0
    uk_holiday = 1.0 if ref_day in UK_MARKET_HOLIDAYS else 0.0

    out["nse_holiday_today"] = nse_holiday
    out["us_holiday_today"] = us_holiday
    out["uk_holiday_today"] = uk_holiday
    out["market_holiday_any"] = 1.0 if max(nse_holiday, us_holiday, uk_holiday) else 0.0

    fomc_days = days_until_next_fomc(ref_day)
    if fomc_days is not None:
        out["fed_meeting_proximity_days"] = fomc_days
        out["fed_meeting_today"] = 1.0 if fomc_days == 0 else 0.0

    out["macro_events_next_7d"] = macro_events_next_days(ref_day, 7)
    out["macro_event_risk_score"] = round(
        out.get("fed_meeting_today", 0.0)
        + out["macro_events_next_7d"] * 0.5
        + out["market_holiday_any"] * 0.25,
        2,
    )

    if corp_body is not None:
        corp_count = _parse_nse_corp_events(corp_body, ref=ref_day)
        out["nse_corp_events_today"] = float(corp_count)

    return out


def europe_session_max_abs_chg(yahoo: dict[str, float]) -> float | None:
    keys = ("global_ftse_chg", "global_cac40_chg", "global_dax_chg", "eu_futures_chg")
    vals = [abs(float(yahoo[k])) for k in keys if k in yahoo and yahoo[k] is not None]
    if not vals:
        return None
    return round(max(vals), 3)


def mock_calendar_fields(ref: date | None = None) -> dict[str, float]:
    ref_day = ref or date.today()
    fomc = days_until_next_fomc(ref_day)
    if fomc is None:
        fomc = 14.0
    return {
        "nse_holiday_today": 0.0,
        "us_holiday_today": 0.0,
        "uk_holiday_today": 0.0,
        "market_holiday_any": 0.0,
        "fed_meeting_proximity_days": fomc,
        "fed_meeting_today": 1.0 if fomc == 0 else 0.0,
        "macro_events_next_7d": macro_events_next_days(ref_day, 7),
        "macro_event_risk_score": 0.5,
        "nse_corp_events_today": 0.0,
        "europe_session_max_abs_chg": 0.22,
    }
