"""Phase 0.1 — Param Chart today-overlay is addressable per instrument.

The overlay key was one slot per tenant, so two Chart windows on different
underlyings overwrote each other's frame. The desk slot (``-``) keeps the
pre-Phase-0 behaviour for callers that do not scope an instrument.
"""

from __future__ import annotations

import pytest

from app.domains import param_chart_cache as pc_cache

NIFTY = "NSE:NIFTY 50"
SENSEX = "BSE:SENSEX"


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    pc_cache.reset_param_chart_cache_for_tests()
    yield
    pc_cache.reset_param_chart_cache_for_tests()


class TestInstrumentSlug:
    def test_blank_is_desk_default(self) -> None:
        assert pc_cache.instrument_slug(None) == pc_cache.DESK_DEFAULT_INSTRUMENT
        assert pc_cache.instrument_slug("  ") == pc_cache.DESK_DEFAULT_INSTRUMENT

    def test_normalizes_case_and_whitespace(self) -> None:
        assert pc_cache.instrument_slug(" bse:sensex ") == SENSEX


@pytest.mark.asyncio
async def test_overlays_do_not_clobber_across_instruments() -> None:
    tenant = "tenant-chart-scope"
    await pc_cache.set_overlay(tenant, {"underlying": NIFTY}, underlying=NIFTY)
    await pc_cache.set_overlay(tenant, {"underlying": SENSEX}, underlying=SENSEX)

    nifty = await pc_cache.get_overlay(tenant, NIFTY)
    sensex = await pc_cache.get_overlay(tenant, SENSEX)

    assert nifty == {"underlying": NIFTY}
    assert sensex == {"underlying": SENSEX}


@pytest.mark.asyncio
async def test_desk_slot_is_independent_of_pinned_instruments() -> None:
    tenant = "tenant-chart-default"
    await pc_cache.set_overlay(tenant, {"underlying": SENSEX}, underlying=SENSEX)

    # An unscoped reader must not pick up a pinned window's frame.
    assert await pc_cache.get_overlay(tenant) is None

    await pc_cache.set_overlay(tenant, {"underlying": "desk"})
    assert await pc_cache.get_overlay(tenant) == {"underlying": "desk"}
    # ...and writing the desk slot must not disturb the pinned one.
    assert await pc_cache.get_overlay(tenant, SENSEX) == {"underlying": SENSEX}


@pytest.mark.asyncio
async def test_unscoped_round_trip_matches_pre_phase0_behaviour() -> None:
    tenant = "tenant-chart-legacy"
    await pc_cache.set_overlay(tenant, {"ok": True})
    assert await pc_cache.get_overlay(tenant) == {"ok": True}
