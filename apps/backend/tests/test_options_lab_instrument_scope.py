"""Phase 0 / Track E — per-instrument stream, cache pointer, and watcher.

E3 exit criterion: two windows, two instruments, one tenant — neither clobbers
the other's chain. Before E1 the fingerprint pointer and watcher were keyed by
tenant+wings only, so the second window overwrote the first.
"""

from __future__ import annotations

import pytest

from app.domains import options_lab_cache as ol_cache
from app.domains.options_lab import (
    OptionsLabConfig,
    chain_frame_from_cache,
    config_for_underlying,
    day_change_from_quote,
    mock_chain_snapshot,
)


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    ol_cache.reset_options_lab_cache_for_tests()
    yield
    ol_cache.reset_options_lab_cache_for_tests()


NIFTY = "NSE:NIFTY 50"
SENSEX = "BSE:SENSEX"


def _config(underlying: str, step: int) -> OptionsLabConfig:
    return OptionsLabConfig(
        underlying_symbol=underlying,
        fut_symbol="NFO:TEST26SEPFUT",
        strike_step=step,
        mock=True,
    )


class TestConfigForUnderlying:
    def test_returns_base_when_no_override(self) -> None:
        base = _config(NIFTY, 50)
        assert config_for_underlying(base, None) is base
        assert config_for_underlying(base, "") is base
        assert config_for_underlying(base, "   ") is base

    def test_returns_base_when_same_symbol(self) -> None:
        base = _config(NIFTY, 50)
        assert config_for_underlying(base, NIFTY) is base

    def test_switches_symbol_label_and_step_from_presets(self) -> None:
        out = config_for_underlying(_config(NIFTY, 50), SENSEX)
        assert out.underlying_symbol == SENSEX
        assert out.underlying_label == "SENSEX"
        assert out.strike_step == 100  # SENSEX preset, not the NIFTY 50 step

    def test_derives_fut_rather_than_inheriting_desk_fut(self) -> None:
        base = _config(NIFTY, 50)
        out = config_for_underlying(base, SENSEX)
        # The desk FUT belongs to the desk underlying — carrying it over would
        # quote NIFTY futures on a SENSEX chain.
        assert out.fut_symbol != base.fut_symbol
        assert "NIFTY" not in out.fut_symbol.upper()

    def test_preserves_shared_desk_flags(self) -> None:
        out = config_for_underlying(_config(NIFTY, 50), SENSEX)
        assert out.mock is True

    def test_fingerprints_differ_per_instrument(self) -> None:
        base = _config(NIFTY, 50)
        assert (
            config_for_underlying(base, SENSEX).cache_fingerprint()
            != base.cache_fingerprint()
        )


class TestInstrumentSlug:
    def test_blank_is_desk_default(self) -> None:
        assert ol_cache.instrument_slug(None) == ol_cache.DESK_DEFAULT_INSTRUMENT
        assert ol_cache.instrument_slug("") == ol_cache.DESK_DEFAULT_INSTRUMENT
        assert ol_cache.instrument_slug("  ") == ol_cache.DESK_DEFAULT_INSTRUMENT

    def test_normalizes_case_and_whitespace(self) -> None:
        assert ol_cache.instrument_slug(" nse:nifty 50 ") == NIFTY


@pytest.mark.asyncio
async def test_fingerprint_pointer_is_per_instrument() -> None:
    tenant = "tenant-scope-fp"
    await ol_cache.remember_fingerprint(
        tenant, wings=5, fingerprint="fp-nifty", underlying=NIFTY
    )
    await ol_cache.remember_fingerprint(
        tenant, wings=5, fingerprint="fp-sensex", underlying=SENSEX
    )

    assert await ol_cache.get_fingerprint(tenant, wings=5, underlying=NIFTY) == "fp-nifty"
    assert (
        await ol_cache.get_fingerprint(tenant, wings=5, underlying=SENSEX) == "fp-sensex"
    )
    # Desk default is its own slot, untouched by either pinned window.
    assert await ol_cache.get_fingerprint(tenant, wings=5) is None


@pytest.mark.asyncio
async def test_watchers_are_per_instrument() -> None:
    tenant = "tenant-scope-watch"
    await ol_cache.touch_watcher(tenant, wings=5, underlying=NIFTY)
    await ol_cache.touch_watcher(tenant, wings=15, underlying=SENSEX)

    watched = await ol_cache.list_watched()
    assert (tenant, 5, NIFTY) in watched
    assert (tenant, 15, SENSEX) in watched

    # Closing one window must not stop the other's warming.
    await ol_cache.clear_watcher(tenant, NIFTY)
    watched = await ol_cache.list_watched()
    assert (tenant, 15, SENSEX) in watched
    assert not any(slug == NIFTY for _t, _w, slug in watched)


@pytest.mark.asyncio
async def test_two_instruments_do_not_clobber_each_other() -> None:
    """E3: the regression this track exists to prevent."""
    tenant = "tenant-scope-frames"
    wings = 5

    for symbol, step in ((NIFTY, 50), (SENSEX, 100)):
        config = _config(symbol, step)
        await ol_cache.set_snapshot(
            tenant,
            mock_chain_snapshot(config, wings=wings),
            wings=wings,
            fingerprint=config.cache_fingerprint(),
            underlying=symbol,
        )

    nifty_frame = await chain_frame_from_cache(tenant, wings=wings, underlying=NIFTY)
    sensex_frame = await chain_frame_from_cache(tenant, wings=wings, underlying=SENSEX)

    assert nifty_frame is not None
    assert sensex_frame is not None
    assert nifty_frame["underlying_symbol"] == NIFTY
    assert sensex_frame["underlying_symbol"] == SENSEX


@pytest.mark.asyncio
async def test_unpinned_window_keeps_pre_e1_behaviour() -> None:
    """No ?underlying= → desk-default slot, unchanged from before E1."""
    tenant = "tenant-scope-default"
    config = _config(NIFTY, 50)
    await ol_cache.set_snapshot(
        tenant,
        mock_chain_snapshot(config, wings=5),
        wings=5,
        fingerprint=config.cache_fingerprint(),
    )

    frame = await chain_frame_from_cache(tenant, wings=5)
    assert frame is not None
    assert frame["underlying_symbol"] == NIFTY
    assert (tenant, 5, ol_cache.DESK_DEFAULT_INSTRUMENT) in await ol_cache.list_watched()


class TestDayChangeFromQuote:
    """Watchlist rows: change vs previous close (Kite-style instrument list)."""

    def test_derives_from_previous_close_not_net_change(self) -> None:
        # Kite returns net_change=0 on equity rows even when the name moved, so
        # two real prices win over the broker's delta.
        change, pct = day_change_from_quote(
            {"net_change": 0, "ohlc": {"close": 1280.0}}, 1287.0
        )
        assert change == 7.0
        assert pct == 0.55

    def test_matches_broker_net_change_when_both_agree(self) -> None:
        change, pct = day_change_from_quote(
            {"net_change": 84.8, "ohlc": {"close": 24090.85}}, 24175.65
        )
        assert change == 84.8
        assert pct == 0.35

    def test_uses_net_change_when_quote_has_no_ohlc(self) -> None:
        change, pct = day_change_from_quote({"net_change": 12.5}, 112.5)
        assert change == 12.5
        assert pct == 12.5

    def test_falls_back_to_spot_minus_close(self) -> None:
        change, pct = day_change_from_quote({"ohlc": {"close": 100.0}}, 110.0)
        assert change == 10.0
        assert pct == 10.0

    def test_negative_change(self) -> None:
        change, pct = day_change_from_quote({"ohlc": {"close": 200.0}}, 190.0)
        assert change == -10.0
        assert pct == -5.0

    def test_missing_close_and_net_change_is_unknown_not_zero(self) -> None:
        # get_ltp responses often omit OHLC — must render "—", never a flat 0.
        assert day_change_from_quote({"last_price": 110.0}, 110.0) == (None, None)
        assert day_change_from_quote(None, 110.0) == (None, None)

    def test_zero_close_does_not_divide_by_zero(self) -> None:
        change, pct = day_change_from_quote({"ohlc": {"close": 0.0}}, 10.0)
        assert change == 10.0
        assert pct is None
