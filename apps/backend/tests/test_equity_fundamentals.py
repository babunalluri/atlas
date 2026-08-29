"""Market cap / P/E parsing from NSE public payloads."""

from __future__ import annotations

import pytest

from app.domains.equity_fundamentals import (
    _parse_index_pe,
    _parse_market_caps,
    _parse_symbol_pe,
    mock_fundamentals,
    read_fundamentals,
    refresh_due,
    refresh_fundamentals,
    reset_fundamentals_for_tests,
    symbol_root,
)


def fetch_fundamentals(symbols, *, index_symbols=(), force=False):
    """Refresh then read — the old single-call shape, for these tests."""
    refresh_fundamentals(symbols, index_symbols=index_symbols, force=force)
    return read_fundamentals(symbols, index_symbols=index_symbols)


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_fundamentals_for_tests()
    yield
    reset_fundamentals_for_tests()


def test_symbol_root_strips_the_exchange() -> None:
    assert symbol_root("NSE:RELIANCE") == "RELIANCE"
    assert symbol_root("BSE:SENSEX") == "SENSEX"
    assert symbol_root("NSE:NIFTY 50") == "NIFTY 50"
    assert symbol_root("  nse:infy ") == "INFY"
    assert symbol_root("") == ""


def test_parse_market_caps_reads_free_float_per_symbol() -> None:
    caps = _parse_market_caps(
        {
            "data": [
                {"symbol": "RELIANCE", "ffmc": "1,234,567.89"},
                {"symbol": "INFY", "ffmc": 654_321.0},
                {"symbol": "NOCAP", "ffmc": "-"},
                {"symbol": "", "ffmc": 100.0},
                "not-a-row",
            ]
        }
    )
    assert caps == {"RELIANCE": 1_234_567.89, "INFY": 654_321.0}


def test_parse_market_caps_tolerates_junk() -> None:
    assert _parse_market_caps(None) == {}
    assert _parse_market_caps({}) == {}
    assert _parse_market_caps({"data": "nope"}) == {}


def test_parse_index_pe_indexes_every_name_form() -> None:
    pes = _parse_index_pe(
        {
            "data": [
                {"indexSymbol": "NIFTY 50", "index": "NIFTY 50", "pe": "22.35"},
                {"indexSymbol": "NIFTY BANK", "pe": 0},
                {"indexSymbol": "NIFTY IT", "pe": "28.1"},
            ]
        }
    )
    assert pes["NIFTY 50"] == 22.35
    assert pes["NIFTY IT"] == 28.1
    # A zero P/E is NSE's "not applicable", not a real ratio.
    assert "NIFTY BANK" not in pes


def test_parse_symbol_pe_prefers_metadata() -> None:
    assert _parse_symbol_pe({"metadata": {"pdSymbolPe": "24.7"}}) == 24.7
    assert _parse_symbol_pe({"info": {"pdSymbolPe": "11.2"}}) == 11.2
    # Loss-making names come back as 0 — no P/E, not a P/E of zero.
    assert _parse_symbol_pe({"metadata": {"pdSymbolPe": 0}}) is None
    assert _parse_symbol_pe({}) is None
    assert _parse_symbol_pe(None) is None


def test_fetch_fundamentals_returns_a_row_per_symbol_when_cold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No network: every asked-for name still gets an entry, so the UI shows —."""
    import app.domains.equity_fundamentals as fundamentals

    monkeypatch.setattr(fundamentals, "_session", lambda: None)

    out = fetch_fundamentals(["NSE:RELIANCE", "NSE:NIFTY 50"])

    assert set(out) == {"NSE:RELIANCE", "NSE:NIFTY 50"}
    assert out["NSE:RELIANCE"] == {"market_cap": None, "pe_ratio": None}


def test_fetch_fundamentals_serves_the_bulk_and_per_symbol_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.domains.equity_fundamentals as fundamentals

    calls: list[str] = []

    def fake_get(path: str, session: object) -> object:
        calls.append(path)
        if "equity-stockIndices" in path:
            return {"data": [{"symbol": "RELIANCE", "ffmc": 1_200_000.0}]}
        if "allIndices" in path:
            return {"data": [{"indexSymbol": "NIFTY 50", "pe": "22.5"}]}
        if "quote-equity" in path:
            return {"metadata": {"pdSymbolPe": "24.75"}}
        return None

    monkeypatch.setattr(fundamentals, "_session", lambda: object())
    monkeypatch.setattr(fundamentals, "_nse_get", fake_get)

    out = fetch_fundamentals(
        ["NSE:RELIANCE", "NSE:NIFTY 50"], index_symbols=("NSE:NIFTY 50",)
    )

    assert out["NSE:RELIANCE"]["market_cap"] == 1_200_000.0
    assert out["NSE:RELIANCE"]["pe_ratio"] == 24.75
    # Indices have an index P/E and no market cap of their own.
    assert out["NSE:NIFTY 50"]["pe_ratio"] == 22.5
    assert out["NSE:NIFTY 50"]["market_cap"] is None
    # One bulk call each, not one per symbol.
    assert sum("equity-stockIndices" in c for c in calls) == 1
    assert sum("allIndices" in c for c in calls) == 1
    # The index must not burn a per-symbol equity call.
    assert not any("NIFTY" in c for c in calls if "quote-equity" in c)


def test_fetch_fundamentals_bounds_per_symbol_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cold 200-name universe must not fire 200 requests at NSE."""
    import app.domains.equity_fundamentals as fundamentals

    quote_calls: list[str] = []

    def fake_get(path: str, session: object) -> object:
        if "quote-equity" in path:
            quote_calls.append(path)
            return {"metadata": {"pdSymbolPe": "10"}}
        return {"data": []}

    monkeypatch.setattr(fundamentals, "_session", lambda: object())
    monkeypatch.setattr(fundamentals, "_nse_get", fake_get)

    fetch_fundamentals([f"NSE:SYM{i}" for i in range(50)])

    assert len(quote_calls) == fundamentals.PE_FILL_BUDGET


def test_mock_fundamentals_cover_every_symbol() -> None:
    out = mock_fundamentals(["NSE:RELIANCE", "NSE:INFY"])
    assert out["NSE:RELIANCE"]["market_cap"] is not None
    assert out["NSE:INFY"]["pe_ratio"] is not None


def test_read_fundamentals_never_touches_the_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The screener request path reads cache only — NSE is minutes-slow cold."""
    import app.domains.equity_fundamentals as fundamentals

    def boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("read_fundamentals must not call NSE")

    monkeypatch.setattr(fundamentals, "_session", boom)
    monkeypatch.setattr(fundamentals, "_nse_get", boom)

    out = read_fundamentals(["NSE:RELIANCE"], index_symbols=())

    assert out == {"NSE:RELIANCE": {"market_cap": None, "pe_ratio": None}}


def test_refresh_due_is_false_while_in_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing NSE must not be re-hit on every screener poll."""
    import app.domains.equity_fundamentals as fundamentals

    assert refresh_due(["NSE:RELIANCE"]) is True

    # No session at all counts as a failure and arms the cooldown.
    monkeypatch.setattr(fundamentals, "_session", lambda: None)
    refresh_fundamentals(["NSE:RELIANCE"])

    assert refresh_due(["NSE:RELIANCE"]) is False


def test_failed_bulk_call_backs_off_instead_of_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.domains.equity_fundamentals as fundamentals

    calls: list[str] = []

    def fake_get(path: str, session: object) -> object:
        calls.append(path)
        # Market cap fails; the per-symbol P/E succeeds.
        if "equity-stockIndices" in path or "allIndices" in path:
            return None
        return {"metadata": {"pdSymbolPe": "12"}}

    monkeypatch.setattr(fundamentals, "_session", lambda: object())
    monkeypatch.setattr(fundamentals, "_nse_get", fake_get)

    refresh_fundamentals(["NSE:RELIANCE"])
    # Progress was made on P/E, but the failed bulk call must still back off —
    # its timestamp stayed 0, so otherwise every pass would retry it.
    assert refresh_due(["NSE:RELIANCE"]) is False
