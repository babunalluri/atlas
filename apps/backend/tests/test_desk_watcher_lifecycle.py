"""Watch keys must expire when the windows that created them close.

Every desk worker gates its tick on "is anyone watching?", so a watch key that
renews itself keeps the whole pipeline — overlay rebuilds, matrix rows, the
Kite subscription — running forever after the last browser closes.
"""

from __future__ import annotations

import pytest

from app.domains import param_chart_cache as pc_cache
from app.domains import signal_engine_cache as signal_cache
from app.domains.equity_fundamentals import (
    FUNDAMENTALS_TTL_SECONDS,
    _pe_fill_due,
)
from app.domains.options_lab_worker import (
    options_lab_source_key,
    stale_lab_source_keys,
    watched_lab_source_keys,
)

TENANT = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(autouse=True)
def _reset() -> None:
    pc_cache.reset_param_chart_cache_for_tests()
    signal_cache.reset_signal_cache_for_tests()
    yield
    pc_cache.reset_param_chart_cache_for_tests()
    signal_cache.reset_signal_cache_for_tests()


async def test_param_chart_refresh_does_not_register_its_own_watcher(monkeypatch) -> None:
    """The worker refreshes what is watched; refreshing must not re-watch it.

    ``refresh_overlay_from_cache`` used to touch the watcher under the config's
    resolved symbol. For a desk-default window that is a *different* slot from
    the one the reader watches, so the worker kept discovering a key nobody
    read, refreshing it, and re-touching it — a watch that never expired.
    """
    from app.domains import param_chart

    async def _no_config(_tenant: str):
        return None

    monkeypatch.setattr(param_chart, "config_from_setup_cache", _no_config)

    await param_chart.refresh_overlay_from_cache(TENANT, underlying=None)

    assert await pc_cache.list_watched() == []


async def test_param_chart_watch_slot_matches_the_reader(monkeypatch) -> None:
    """A desk-default reader and its overlay must share one slot."""
    from app.domains import param_chart

    async def _no_overlay(_tenant: str, _underlying=None):
        return None

    monkeypatch.setattr(pc_cache, "get_overlay", _no_overlay)

    await param_chart.overlay_frame_from_cache(TENANT, None)

    watched = await pc_cache.list_watched()
    assert watched == [(TENANT, None)]


async def test_signal_watch_instrument_clears_on_disconnect() -> None:
    """Closing one board drops that row, not the tenant or its siblings."""
    await signal_cache.touch_watcher(TENANT, instrument="NSE:RELIANCE")
    await signal_cache.touch_watcher(TENANT, instrument="NSE:NIFTY 50")
    assert len(await signal_cache.watched_instrument_symbols(TENANT)) == 2

    await signal_cache.clear_watch_instrument(TENANT, "NSE:RELIANCE")

    remaining = await signal_cache.watched_instrument_symbols(TENANT)
    assert list(remaining.values()) == ["NSE:NIFTY 50"]
    # The tenant watcher itself is untouched — other windows may still be open.
    assert await signal_cache.watcher_alive(TENANT) is True


async def test_signal_clear_watch_instrument_tolerates_no_instrument() -> None:
    await signal_cache.touch_watcher(TENANT, instrument="NSE:NIFTY 50")
    await signal_cache.clear_watch_instrument(TENANT, None)
    assert len(await signal_cache.watched_instrument_symbols(TENANT)) == 1


def test_lab_source_key_matches_the_watch_slot() -> None:
    """A window that pins nothing subscribes under the same '-' slot it watches.

    Keying the subscription by the resolved symbol instead made the prune treat
    a live desk-default window's own tokens as stale on every tick.
    """
    watched = [(TENANT, 15, "-")]
    live = watched_lab_source_keys(watched)

    assert options_lab_source_key("-") in live[TENANT]
    assert stale_lab_source_keys([options_lab_source_key("-")], live[TENANT]) == []


def test_lab_prune_leaves_other_desks_alone() -> None:
    keys = ["options_lab:NSE:NIFTY 50", "signal", "param_chart"]
    stale = stale_lab_source_keys(keys, keep=set())
    assert stale == ["options_lab:NSE:NIFTY 50"]


def test_pe_fill_is_due_once_per_ttl_even_with_no_pe() -> None:
    """A loss-making name has no P/E; it must not be re-fetched every pass.

    Testing ``pe is None`` first made those names permanently stale, which also
    kept ``refresh_due`` True on every screener request.
    """
    now = 1_000_000.0
    assert _pe_fill_due(None, now) is True
    assert _pe_fill_due({"market_cap": 1.0}, now) is True

    attempted = {"market_cap": 1.0, "pe": None, "at": now}
    assert _pe_fill_due(attempted, now) is False
    assert _pe_fill_due(attempted, now + FUNDAMENTALS_TTL_SECONDS + 1) is True


def test_signal_config_takes_the_instrument_from_the_shared_board() -> None:
    """Every consumer, not just the admin UI, reads the board's instrument.

    The merge used to live only in ``get_admin_config``, so the ticker and the
    matrix row builds kept using a stale signal nest — the desk showed one
    instrument while the engine evaluated another.
    """
    from app.domains.signal_engine import config_with_desk_board

    cfg = config_with_desk_board(
        {
            "underlying_symbol": "NSE:NIFTY 50",
            "nifty_fut_symbol": "NFO:NIFTY25AUGFUT",
            "strike_step": 50,
            "desk_instrument": {
                "underlying_symbol": "NSE:NIFTY BANK",
                "underlying_label": "BANKNIFTY",
                "fut_symbol": "NFO:BANKNIFTY25AUGFUT",
                "strike_step": 100,
            },
        }
    )

    assert cfg.underlying_symbol == "NSE:NIFTY BANK"
    assert cfg.underlying_label == "BANKNIFTY"
    # The legacy alias must not outrank the board's FUT.
    assert cfg.nifty_fut_symbol == "NFO:BANKNIFTY25AUGFUT"
    assert cfg.strike_step == 100


def test_board_does_not_override_signals_auto_rolled_legs() -> None:
    """Signal owns CE/PE while the instrument is unchanged.

    The board's CE/PE are whatever someone last typed in a manual identity
    PATCH; Signal rolls ATM legs itself and never writes them back. Letting the
    board win would pin the engine to a stale strike.
    """
    from app.domains.signal_engine import config_with_desk_board

    cfg = config_with_desk_board(
        {
            "underlying_symbol": "NSE:NIFTY 50",
            "nifty_fut_symbol": "NFO:NIFTY25AUGFUT",
            "ce_symbol": "NFO:NIFTY25AUG24500CE",
            "pe_symbol": "NFO:NIFTY25AUG24500PE",
            "desk_instrument": {
                "underlying_symbol": "NSE:NIFTY 50",
                "fut_symbol": "NFO:NIFTY25AUGFUT",
                "ce_symbol": "NFO:NIFTY25AUG23000CE",
                "pe_symbol": "NFO:NIFTY25AUG23000PE",
            },
        }
    )

    assert cfg.ce_symbol == "NFO:NIFTY25AUG24500CE"
    assert cfg.pe_symbol == "NFO:NIFTY25AUG24500PE"


def test_no_board_leaves_the_signal_nest_untouched() -> None:
    from app.domains.signal_engine import config_with_desk_board

    cfg = config_with_desk_board(
        {"underlying_symbol": "NSE:NIFTY 50", "strike_step": 50}
    )
    assert cfg.underlying_symbol == "NSE:NIFTY 50"
    assert cfg.strike_step == 50
