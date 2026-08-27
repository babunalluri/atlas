"""Redis signal matrix: shared globals + per-instrument rows.

Switching underlyings becomes "subscribe to another row key" instead of a
structural flush + cold rebuild. SSE merges ``globals + row[selected]``.
"""

from __future__ import annotations

import re
from typing import Any

from app.domains.trade_desk_checklist import CHECKLIST_CATEGORIES

# Default pinned desk rows (config-driven list can grow later — keep ≤3 until
# first-party Kite REST + quote coalescing land).
DEFAULT_PINNED_INSTRUMENTS: tuple[str, ...] = (
    "NSE:NIFTY 50",
    "BSE:SENSEX",
    "NSE:NIFTY BANK",
)

# Categories that stay in the shared globals blob (not instrument-specific).
_GLOBAL_CATEGORIES = frozenset(
    {
        "Global Markets Watch",
        "Stock Big-Move Watch",
        "Timing & No-Trade Rules",
        "Trade Discipline Check",
    }
)

# Top-level snapshot keys that belong on the shared globals document.
_GLOBAL_TOP_KEYS = frozenset(
    {
        "engine_enabled",
        "engine_active",
        "mock",
        "live",
        "has_broker",
        "team_slug",
        "stream",
        "poll_ms",
        "broker_poll_ms",
        "ticker",
        "config_epoch",
        # Shared market context warnings that are not row-specific.
        "live_warnings_global",
        # Stamp so SSE dedupe notices globals refreshing on their own cadence.
        "globals_computed_at_ms",
    }
)

# Top-level keys that belong on an instrument row.
_ROW_TOP_KEYS = frozenset(
    {
        "underlying",
        "ce_symbol",
        "pe_symbol",
        "atm",
        "entry",
        "entry_ready",
        "passed",
        "evaluable",
        "feed_source",
        "live_quote_missing",
        "live_warnings",
        "computed_at_ms",
        "snapshot_stale",
        "engine_computing",
        "data_age_ms",
        "instrument",
    }
)

_SAFE_KEY_RE = re.compile(r"[^A-Za-z0-9:_-]+")


def instrument_key(symbol: str) -> str:
    """Normalize an exchange:symbol into a Redis-safe row suffix."""
    raw = (symbol or "").strip()
    if not raw:
        return ""
    # Preserve exchange:root shape; collapse spaces.
    collapsed = raw.replace(" ", "_")
    return _SAFE_KEY_RE.sub("_", collapsed)


# Per-instrument Tier-B caches (must not bleed NIFTY PCR onto a SENSEX row).
ROW_SCOPED_METRIC_KINDS: tuple[str, ...] = (
    "levels",
    "trend",
    "atm_iv",
    "option_chain",
)


def row_metric_id(kind: str, underlying: str | None) -> str:
    """``levels`` → ``levels:NSE:NIFTY_50`` when an underlying is set."""
    base = (kind or "").strip()
    key = instrument_key(underlying or "")
    if not base:
        return key
    if not key:
        return base
    return f"{base}:{key}"


def is_row_scoped_metric_id(metric_id: str) -> bool:
    mid = (metric_id or "").strip()
    if mid in ROW_SCOPED_METRIC_KINDS:
        return True
    return any(mid.startswith(f"{kind}:") for kind in ROW_SCOPED_METRIC_KINDS)


def pinned_instruments(settings: dict[str, Any] | None = None) -> list[str]:
    """Return the pinned matrix symbols (config override or defaults)."""
    raw = None
    if isinstance(settings, dict):
        raw = settings.get("pinned_instruments") or settings.get("signal_pinned_instruments")
    if isinstance(raw, list) and raw:
        out = [str(item).strip() for item in raw if str(item).strip()]
        if out:
            return out
    return list(DEFAULT_PINNED_INSTRUMENTS)


def is_global_metric(row: dict[str, Any]) -> bool:
    category = str(row.get("category") or "")
    if category in _GLOBAL_CATEGORIES:
        return True
    # Defensive: unknown categories stay on the row so instrument boards stay complete.
    if category and category not in CHECKLIST_CATEGORIES:
        return False
    return category in _GLOBAL_CATEGORIES


def split_snapshot(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a full desk snapshot into (globals, row) documents."""
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), list) else []
    global_metrics = [m for m in metrics if isinstance(m, dict) and is_global_metric(m)]
    row_metrics = [m for m in metrics if isinstance(m, dict) and not is_global_metric(m)]

    globals_doc: dict[str, Any] = {"metrics": global_metrics}
    for key in _GLOBAL_TOP_KEYS:
        if key in payload:
            globals_doc[key] = payload[key]
    # Always stamp engine flags when present under alternate names.
    for key in ("engine_enabled", "engine_active", "mock", "live", "has_broker"):
        if key in payload:
            globals_doc[key] = payload[key]
    # Primary compute stamps globals so row-only refreshes still revise SSE.
    if "computed_at_ms" in payload and "globals_computed_at_ms" not in globals_doc:
        globals_doc["globals_computed_at_ms"] = payload["computed_at_ms"]

    underlying = payload.get("underlying") if isinstance(payload.get("underlying"), dict) else {}
    instrument = str(
        payload.get("instrument")
        or underlying.get("symbol")
        or ""
    ).strip()

    row_doc: dict[str, Any] = {
        "metrics": row_metrics,
        "instrument": instrument,
    }
    for key in _ROW_TOP_KEYS:
        if key in payload:
            row_doc[key] = payload[key]
    # Pass / evaluable should reflect the merged board, but keep row copies for
    # warm switches (UI may show row-only until globals arrive).
    for key in ("passed", "evaluable", "entry", "entry_ready"):
        if key in payload:
            row_doc[key] = payload[key]
    if underlying:
        row_doc["underlying"] = underlying
    return globals_doc, row_doc


def merge_globals_row(
    globals_doc: dict[str, Any] | None,
    row_doc: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Merge globals + selected row into one SSE desk frame.

    Returns None when there is nothing useful to merge (caller should fall
    back to the monolithic snapshot).
    """
    if not row_doc and not globals_doc:
        return None
    if not row_doc:
        # Globals alone omit row fields (passed/entry/underlying) — incomplete.
        return None
    g = dict(globals_doc or {})
    r = dict(row_doc)
    g_metrics = g.pop("metrics", []) if isinstance(g.get("metrics"), list) else []
    r_metrics = r.pop("metrics", []) if isinstance(r.get("metrics"), list) else []
    # Preserve checklist order: globals categories first where possible, then row.
    merged_metrics = [*g_metrics, *r_metrics]
    out: dict[str, Any] = {**g, **r, "metrics": merged_metrics}
    # Keep the row's gating counts from evaluate_signal_state — do not recompute
    # over every non-null metric (that inflates the header vs legacy boards).
    if "passed" in r:
        out["passed"] = r["passed"]
    if "evaluable" in r:
        out["evaluable"] = r["evaluable"]
    out["matrix"] = True
    return out


def config_for_instrument(
    primary: Any,
    *,
    symbol: str,
    label: str | None = None,
    strike_step: int | None = None,
    fut_symbol: str | None = None,
) -> Any:
    """Clone primary SignalEngineConfig aimed at another underlying.

    CE/PE left empty so auto-ATM can resolve per row. Shared desk flags
    (mock, engine_enabled, India VIX symbol) stay; per-underlying manuals
    (PCR / max pain / IVP / OI% / IV chg) are cleared so NIFTY overrides
    cannot bleed onto SENSEX rows.
    """
    from dataclasses import replace

    from app.domains.options_lab import suggest_fut_symbol

    sym = (symbol or "").strip()
    if not sym:
        return primary
    fut = (fut_symbol or "").strip() or suggest_fut_symbol(sym) or ""
    step = strike_step if strike_step is not None else getattr(primary, "strike_step", 50)
    return replace(
        primary,
        underlying_symbol=sym,
        underlying_label=(label or sym),
        nifty_fut_symbol=fut,
        ce_symbol="",
        pe_symbol="",
        strike_step=int(step) if step else 50,
        pcr=None,
        max_pain=None,
        ivp=None,
        oi_pct_chg=None,
        iv_chg=None,
    )
