"""Param Chart — shared checklist allowlist (customer params pack).

Curated from unique customer WhatsApp annotations (checklist numbers only).
Category dropdowns filter within this allowlist only — not the full 115 Trade Desk items.
"""

from __future__ import annotations

from typing import Any

from app.domains.trade_desk_checklist import DEFAULT_METRICS

# Nested under Signal engine tool settings (same pattern as Options Lab).
PARAM_CHART_SETTINGS_KEY = "param_chart"

# Checklist numbers from customer shared params photos (deduped annotations).
PARAM_CHART_SHARED_CHECK_NOS: frozenset[int] = frozenset(
    {
        1,
        7,
        8,
        10,
        15,
        16,
        17,
        18,
        19,
        23,
        26,
        27,
        33,
        36,
        41,
        42,
        44,
        45,
        48,
        49,
        50,
        51,
        52,
        57,
        59,
        61,
        64,
        68,
        69,
        70,
    }
)

DEFAULT_PARAM_CHART_CONFIG: dict[str, Any] = {
    "underlying_symbol": "NSE:NIFTY BANK",
    "underlying_label": "BANKNIFTY",
    "fut_symbol": "",
    "strike_step": 100,
    "strike": None,
    "entry_ce_premium": 900.0,
    "entry_pe_premium": 900.0,
    "ce_symbol": "",
    "pe_symbol": "",
    "year": None,
    "month": None,
    # TradingView-style resolution: 1m | 5m | 15m | 1H | 1D | 1W | 1M
    # Note: ``1m`` = minute (case-sensitive); ``1M`` = monthly.
    "interval": "1D",
}

# UI label → Kite get_historical_candles interval (+ how we bucket the pack).
PARAM_CHART_INTERVALS: tuple[dict[str, str], ...] = (
    {"id": "1m", "label": "1m", "kite": "minute"},
    {"id": "5m", "label": "5m", "kite": "5minute"},
    {"id": "15m", "label": "15m", "kite": "15minute"},
    {"id": "1H", "label": "1H", "kite": "60minute"},
    {"id": "1D", "label": "1D", "kite": "day"},
    {"id": "1W", "label": "1W", "kite": "day"},  # aggregated from daily
    {"id": "1M", "label": "1M", "kite": "day"},  # aggregated from daily
)

PARAM_CHART_INTERVAL_IDS: frozenset[str] = frozenset(
    p["id"] for p in PARAM_CHART_INTERVALS
)


def normalize_param_chart_interval(raw: object) -> str:
    """Canonical UI interval id.

    ``1m`` (minute) and ``1M`` (month) must stay distinct — never blind-``.upper()``.
    """
    s = str(raw or "1D").strip()
    if s in PARAM_CHART_INTERVAL_IDS:
        return s
    low = s.lower()
    if low in ("1m", "min", "minute", "1min", "1minute"):
        return "1m"
    if low in ("5m", "5min", "5minute"):
        return "5m"
    if low in ("15m", "15min", "15minute"):
        return "15m"
    upper = s.upper()
    if upper in PARAM_CHART_INTERVAL_IDS:
        return upper
    aliases = {
        "D": "1D",
        "DAY": "1D",
        "DAILY": "1D",
        "H": "1H",
        "HOUR": "1H",
        "HOURLY": "1H",
        "60MINUTE": "1H",
        "W": "1W",
        "WEEK": "1W",
        "WEEKLY": "1W",
        "M": "1M",
        "MONTH": "1M",
        "MONTHLY": "1M",
    }
    return aliases.get(upper, "1D")


def shared_metric_defs() -> list[dict[str, Any]]:
    """Ordered shared metrics for Param Chart (id, check_no, category, label)."""
    out: list[dict[str, Any]] = []
    for row in DEFAULT_METRICS:
        try:
            check_no = int(row.get("check_no") or 0)
        except (TypeError, ValueError):
            continue
        if check_no not in PARAM_CHART_SHARED_CHECK_NOS:
            continue
        out.append(
            {
                "id": str(row["id"]),
                "check_no": check_no,
                "category": str(row.get("category") or ""),
                "label": str(row.get("label") or row["id"]),
                "rule": row.get("rule"),
                "hint": row.get("hint"),
            }
        )
    out.sort(key=lambda m: (m["check_no"], m["id"]))
    return out


def shared_metric_ids() -> frozenset[str]:
    return frozenset(m["id"] for m in shared_metric_defs())


def shared_categories() -> list[str]:
    seen: list[str] = []
    for m in shared_metric_defs():
        cat = m["category"]
        if cat and cat not in seen:
            seen.append(cat)
    return seen


def project_metrics_from_signal_rows(
    rows: list[dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    """Filter Signal ``metrics[]`` rows down to the shared allowlist."""
    allow = shared_metric_ids()
    out: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        mid = str(row.get("id") or "")
        if mid not in allow:
            continue
        out[mid] = {
            "id": mid,
            "value": row.get("value"),
            "passed": row.get("passed"),
            "label": row.get("label"),
            "category": row.get("category"),
            "check_no": row.get("check_no"),
        }
    return out
