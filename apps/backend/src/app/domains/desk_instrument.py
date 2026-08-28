"""Shared desk instrument board — one identity layer for all trading desks.

Stored under the signal-engine tool settings as ``desk_instrument``. Signal
matrix rows hold per-instrument checklist metrics; this blob holds the desk-wide
"what are we trading?" identity (underlying, FUT, ATM CE/PE) that Param Chart,
Options Lab, and Signal setup can read without each cold-starting alone.
"""

from __future__ import annotations

import time
from typing import Any

DESK_INSTRUMENT_SETTINGS_KEY = "desk_instrument"

IDENTITY_FIELDS: tuple[str, ...] = (
    "underlying_symbol",
    "underlying_label",
    "fut_symbol",
    "strike_step",
    "ce_symbol",
    "pe_symbol",
    "atm",
)

# Param Chart nest field names (subset of identity).
_DESK_INSTRUMENT_TO_CHART: dict[str, str] = {
    "underlying_symbol": "underlying_symbol",
    "underlying_label": "underlying_label",
    "fut_symbol": "fut_symbol",
    "strike_step": "strike_step",
    "ce_symbol": "ce_symbol",
    "pe_symbol": "pe_symbol",
    "atm": "strike",
}


def read_desk_board(settings: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the shared board dict when it has an underlying."""
    if not isinstance(settings, dict):
        return None
    raw = settings.get(DESK_INSTRUMENT_SETTINGS_KEY)
    if not isinstance(raw, dict):
        return None
    underlying = str(raw.get("underlying_symbol") or "").strip()
    if not underlying:
        return None
    return dict(raw)


def board_from_mapping(
    mapping: dict[str, Any],
    *,
    source: str,
    atm: int | float | None = None,
) -> dict[str, Any]:
    """Build a desk board document from Signal / Lab / Chart config fields."""
    underlying = str(mapping.get("underlying_symbol") or "").strip()
    board: dict[str, Any] = {
        "underlying_symbol": underlying,
        "underlying_label": str(
            mapping.get("underlying_label") or underlying
        ).strip(),
        "fut_symbol": str(mapping.get("fut_symbol") or "").strip(),
        "updated_at_ms": int(time.time() * 1000),
        "source": source,
    }
    step = mapping.get("strike_step")
    if step not in (None, ""):
        try:
            board["strike_step"] = int(step)
        except (TypeError, ValueError):
            pass
    ce = str(mapping.get("ce_symbol") or "").strip()
    pe = str(mapping.get("pe_symbol") or "").strip()
    if ce:
        board["ce_symbol"] = ce
    if pe:
        board["pe_symbol"] = pe
    atm_val = atm if atm is not None else mapping.get("atm") or mapping.get("strike")
    if atm_val not in (None, ""):
        try:
            board["atm"] = int(round(float(atm_val)))
        except (TypeError, ValueError):
            pass
    return board


def board_changed(
    previous: dict[str, Any] | None,
    next_board: dict[str, Any],
) -> bool:
    """True when identity fields differ (ignores timestamps)."""
    prev = previous or {}
    for key in IDENTITY_FIELDS:
        old = prev.get(key)
        new = next_board.get(key)
        if key == "strike_step":
            try:
                old_n = int(old) if old not in (None, "") else None
            except (TypeError, ValueError):
                old_n = None
            try:
                new_n = int(new) if new not in (None, "") else None
            except (TypeError, ValueError):
                new_n = None
            if old_n != new_n:
                return True
            continue
        if str(old if old is not None else "").strip() != str(
            new if new is not None else ""
        ).strip():
            return True
    return False


def desk_instrument_tool_patch(
    board: dict[str, Any],
    previous_settings: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Tool-settings patch for ``desk_instrument``, or None when unchanged."""
    prev_board = read_desk_board(previous_settings)
    if not board_changed(prev_board, board):
        return None
    return {DESK_INSTRUMENT_SETTINGS_KEY: board}


def merge_board_into_mapping(
    target: dict[str, Any],
    board: dict[str, Any] | None,
    *,
    fill_only: bool = True,
) -> dict[str, Any]:
    """Merge shared board identity into a desk config dict."""
    if not board:
        return target
    out = dict(target)
    for key in IDENTITY_FIELDS:
        if key == "atm":
            continue
        if fill_only and str(out.get(key) or "").strip():
            continue
        val = board.get(key)
        if val is None or val == "":
            continue
        out[key] = val
    return out


def merge_desk_instrument_into_chart(settings: dict[str, Any] | None) -> dict[str, Any]:
    """Resolve Param Chart instrument fields (board wins over param_chart nest)."""
    from app.domains.param_chart_constants import PARAM_CHART_SETTINGS_KEY

    blob = settings if isinstance(settings, dict) else {}
    nested = blob.get(PARAM_CHART_SETTINGS_KEY)
    merged = dict(nested) if isinstance(nested, dict) else {}
    desk = read_desk_board(blob)
    if desk:
        for src, dst in _DESK_INSTRUMENT_TO_CHART.items():
            val = desk.get(src)
            if val is not None and val != "":
                merged[dst] = val
    return merged


def patch_touches_identity(patch: dict[str, Any]) -> bool:
    """True when a desk config PATCH may update the shared board."""
    identity_patch_keys = set(IDENTITY_FIELDS) - {"atm"}
    identity_patch_keys.add("strike")  # Param Chart ATM strike
    return any(key in patch for key in identity_patch_keys)
