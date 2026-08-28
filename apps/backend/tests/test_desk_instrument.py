"""Tests for shared desk instrument board helpers."""

from __future__ import annotations

from app.domains.desk_instrument import (
    DESK_INSTRUMENT_SETTINGS_KEY,
    board_from_mapping,
    board_changed,
    desk_instrument_tool_patch,
    merge_board_into_mapping,
    merge_desk_instrument_into_chart,
    merge_desk_instrument_into_signal,
    read_desk_board,
)
from app.domains.param_chart_constants import PARAM_CHART_SETTINGS_KEY


def test_read_desk_board_requires_underlying() -> None:
    assert read_desk_board({DESK_INSTRUMENT_SETTINGS_KEY: {}}) is None
    board = read_desk_board(
        {DESK_INSTRUMENT_SETTINGS_KEY: {"underlying_symbol": "NSE:NIFTY 50"}}
    )
    assert board is not None
    assert board["underlying_symbol"] == "NSE:NIFTY 50"


def test_merge_board_fill_only_when_lab_empty() -> None:
    board = board_from_mapping(
        {
            "underlying_symbol": "NSE:NIFTY 50",
            "underlying_label": "NIFTY 50",
            "fut_symbol": "NFO:NIFTY26AUGFUT",
            "strike_step": 50,
        },
        source="signal",
    )
    merged = merge_board_into_mapping({}, board, fill_only=True)
    assert merged["underlying_symbol"] == "NSE:NIFTY 50"
    assert merged["fut_symbol"] == "NFO:NIFTY26AUGFUT"

    keep = merge_board_into_mapping(
        {"underlying_symbol": "BSE:SENSEX", "fut_symbol": "BFO:SENSEX26AUGFUT"},
        board,
        fill_only=True,
    )
    assert keep["underlying_symbol"] == "BSE:SENSEX"


def test_desk_instrument_tool_patch_skips_empty_underlying() -> None:
    board = board_from_mapping({"underlying_symbol": ""}, source="signal")
    assert desk_instrument_tool_patch(board, {}) is None


def test_desk_instrument_tool_patch_skips_unchanged() -> None:
    board = board_from_mapping(
        {"underlying_symbol": "NSE:NIFTY 50", "fut_symbol": "NFO:NIFTY26AUGFUT"},
        source="signal",
    )
    prev = {DESK_INSTRUMENT_SETTINGS_KEY: dict(board)}
    assert desk_instrument_tool_patch(board, prev) is None
    board2 = {**board, "fut_symbol": "NFO:NIFTY26SEPFUT"}
    patch = desk_instrument_tool_patch(board2, prev)
    assert patch is not None
    assert patch[DESK_INSTRUMENT_SETTINGS_KEY]["fut_symbol"] == "NFO:NIFTY26SEPFUT"


def test_merge_desk_instrument_into_chart_prefers_board() -> None:
    merged = merge_desk_instrument_into_chart(
        {
            PARAM_CHART_SETTINGS_KEY: {
                "underlying_symbol": "NSE:NIFTY BANK",
                "year": 2026,
                "month": 8,
            },
            DESK_INSTRUMENT_SETTINGS_KEY: {
                "underlying_symbol": "NSE:NIFTY 50",
                "ce_symbol": "NFO:NIFTY26AUG24000CE",
            },
        }
    )
    assert merged["underlying_symbol"] == "NSE:NIFTY 50"
    assert merged["ce_symbol"] == "NFO:NIFTY26AUG24000CE"
    assert merged["year"] == 2026


def test_merge_desk_instrument_into_signal_prefers_board() -> None:
    merged = merge_desk_instrument_into_signal(
        {
            "underlying_symbol": "NSE:NIFTY 50",
            "underlying_label": "NIFTY",
            "fut_symbol": "NFO:NIFTY26SEPFUT",
            "strike_step": 50,
            "ce_symbol": "NFO:NIFTY26SEP24500CE",
            "pe_symbol": "NFO:NIFTY26SEP24500PE",
        },
        {
            DESK_INSTRUMENT_SETTINGS_KEY: {
                "underlying_symbol": "BSE:SENSEX",
                "underlying_label": "SENSEX",
                "fut_symbol": "BFO:SENSEX26SEPFUT",
                "strike_step": 100,
            }
        },
    )
    assert merged["underlying_symbol"] == "BSE:SENSEX"
    assert merged["fut_symbol"] == "BFO:SENSEX26SEPFUT"
    assert merged["ce_symbol"] == ""
    assert merged["pe_symbol"] == ""


def test_board_changed_detects_strike_step() -> None:
    prev = board_from_mapping(
        {"underlying_symbol": "NSE:NIFTY 50", "strike_step": 50},
        source="signal",
    )
    nxt = board_from_mapping(
        {"underlying_symbol": "NSE:NIFTY 50", "strike_step": 100},
        source="signal",
    )
    assert board_changed(prev, nxt) is True


def test_matrix_row_carries_nifty_ltp_for_chain_seed() -> None:
    from app.domains.signal_matrix import merge_globals_row, split_snapshot

    payload = {
        "underlying": {"symbol": "NSE:NIFTY 50", "label": "NIFTY 50"},
        "instrument": "NSE:NIFTY 50",
        "atm": 24500,
        "nifty_ltp": 24487.5,
        "ce_symbol": "NFO:NIFTY26AUG24500CE",
        "metrics": [],
    }
    globals_doc, row_doc = split_snapshot(payload)
    merged = merge_globals_row(globals_doc, row_doc)
    assert merged is not None
    assert merged.get("nifty_ltp") == 24487.5
    assert merged.get("atm") == 24500
