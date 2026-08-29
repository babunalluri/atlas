"""Param Chart request scoping — two windows, two instruments, no collisions."""

from __future__ import annotations

import pytest

from app.domains import param_chart_cache as pc_cache
from app.domains.param_chart import ParamChartConfig, config_for_underlying


@pytest.fixture(autouse=True)
def _reset() -> None:
    pc_cache.reset_param_chart_cache_for_tests()
    yield
    pc_cache.reset_param_chart_cache_for_tests()


def test_config_for_underlying_clears_the_previous_instrument_legs() -> None:
    base = ParamChartConfig(
        underlying_symbol="NSE:NIFTY BANK",
        underlying_label="BANKNIFTY",
        strike_step=100,
        strike=57000,
        ce_symbol="NFO:BANKNIFTY26AUG57000CE",
        pe_symbol="NFO:BANKNIFTY26AUG57000PE",
    )
    scoped = config_for_underlying(base, "NSE:NIFTY 50")

    assert scoped.underlying_symbol == "NSE:NIFTY 50"
    assert scoped.underlying_label == "NIFTY 50"
    assert scoped.strike_step == 50
    # A BANKNIFTY strike and its legs are meaningless on NIFTY.
    assert scoped.strike is None
    assert scoped.ce_symbol == ""
    assert scoped.pe_symbol == ""
    # The desk config itself is untouched — scoping is request-local.
    assert base.underlying_symbol == "NSE:NIFTY BANK"
    assert base.strike == 57000


def test_config_for_underlying_is_identity_for_the_same_or_empty_symbol() -> None:
    base = ParamChartConfig(underlying_symbol="NSE:NIFTY 50")
    assert config_for_underlying(base, "NSE:NIFTY 50") is base
    assert config_for_underlying(base, "") is base
    assert config_for_underlying(base, None) is base


def test_config_for_underlying_carries_equity_strike_steps() -> None:
    base = ParamChartConfig(underlying_symbol="NSE:NIFTY BANK", strike_step=100)
    assert config_for_underlying(base, "NSE:RELIANCE").strike_step == 20
    assert config_for_underlying(base, "NSE:ITC").strike_step == 5
    # Unknown names keep the desk step rather than guessing.
    assert config_for_underlying(base, "NSE:NOTLISTED").strike_step == 100


@pytest.mark.asyncio
async def test_month_packs_do_not_collide_across_instruments() -> None:
    """Two Chart windows on different underlyings had one shared pack slot."""
    tenant = "tenant-a"
    for symbol, close in (("NSE:NIFTY 50", 24_150.0), ("BSE:SENSEX", 81_200.0)):
        await pc_cache.set_month_pack(
            tenant,
            year=2026,
            month=8,
            interval="1D",
            underlying=symbol,
            payload={"days": [{"date": "2026-08-28", "close": close}]},
        )

    nifty = await pc_cache.get_month_pack(
        tenant, year=2026, month=8, interval="1D", underlying="NSE:NIFTY 50"
    )
    sensex = await pc_cache.get_month_pack(
        tenant, year=2026, month=8, interval="1D", underlying="BSE:SENSEX"
    )
    assert nifty is not None and sensex is not None
    assert nifty["days"][0]["close"] == 24_150.0
    assert sensex["days"][0]["close"] == 81_200.0


@pytest.mark.asyncio
async def test_watchers_are_listed_per_instrument() -> None:
    await pc_cache.touch_watcher("tenant-a", underlying="NSE:NIFTY 50")
    await pc_cache.touch_watcher("tenant-a", underlying="BSE:SENSEX")
    await pc_cache.touch_watcher("tenant-b")

    pairs = await pc_cache.list_watched()
    assert ("tenant-a", "NSE:NIFTY 50") in pairs
    assert ("tenant-a", "BSE:SENSEX") in pairs
    # A window on the desk instrument reports None, not a slug.
    assert ("tenant-b", None) in pairs

    # The worker's Kite sync is per tenant, so it still needs unique ids.
    tenants = await pc_cache.list_watched_tenant_ids()
    assert sorted(tenants) == ["tenant-a", "tenant-b"]


@pytest.mark.asyncio
async def test_rebuild_lock_is_per_instrument() -> None:
    """A SENSEX rebuild must not block a NIFTY one."""
    first = await pc_cache.try_rebuild_lock(
        "tenant-a", year=2026, month=8, interval="1D", underlying="NSE:NIFTY 50"
    )
    other = await pc_cache.try_rebuild_lock(
        "tenant-a", year=2026, month=8, interval="1D", underlying="BSE:SENSEX"
    )
    assert first is True
    assert other is True


@pytest.mark.asyncio
async def test_ticker_symbols_union_every_watched_instrument() -> None:
    from app.domains.param_chart_worker import _watch_symbols_for_instruments

    cfg = ParamChartConfig(underlying_symbol="NSE:NIFTY BANK", strike_step=100)
    symbols = await _watch_symbols_for_instruments(
        "tenant-a", cfg, ("NSE:NIFTY BANK", "NSE:NIFTY 50")
    )
    # Both instruments' spot legs must reach the shared Kite hub, or the
    # scoped window reads REST while the desk one reads the book.
    assert "NSE:NIFTY BANK" in symbols
    assert "NSE:NIFTY 50" in symbols
    assert len(symbols) == len(set(symbols))
