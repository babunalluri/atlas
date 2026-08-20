"""Tests for Options Lab IV history and IVP."""

from __future__ import annotations

import pytest

from app.domains import signal_engine_cache as cache
from app.domains.options_lab_iv import (
    IVP_MIN_SAMPLES,
    compute_ivp,
    generate_mock_iv_history,
    ivp_for_symbol,
    record_atm_iv,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    cache.reset_signal_cache_for_tests()
    yield
    cache.reset_signal_cache_for_tests()


def test_compute_ivp_percentile() -> None:
    samples = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
    assert compute_ivp(samples, 10.0) == 0.0
    assert compute_ivp(samples, 15.0) == 83.3
    assert compute_ivp(samples[: IVP_MIN_SAMPLES - 1], 12.0) is None


def test_generate_mock_iv_history_length() -> None:
    rows = generate_mock_iv_history("NSE:NIFTY 50", 12.0, days=30)
    assert len(rows) == 30
    assert rows[-1]["iv"] >= 4.0


@pytest.mark.asyncio
async def test_record_atm_iv_updates_same_day() -> None:
    tenant = "tenant-test"
    first = await record_atm_iv(tenant, "NSE:NIFTY 50", 11.0, mock=True)
    second = await record_atm_iv(tenant, "NSE:NIFTY 50", 11.5, mock=True)
    assert len(second) == len(first)
    assert second[-1]["iv"] == 11.5


@pytest.mark.asyncio
async def test_ivp_for_symbol_mock_seeds_history() -> None:
    tenant = "tenant-test"
    ivp = await ivp_for_symbol(tenant, "NSE:BANKNIFTY", 14.0, mock=True)
    assert ivp is not None
    assert 0 <= ivp <= 100
