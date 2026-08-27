"""Signal matrix globals/row split + merge tests."""

from __future__ import annotations

import pytest

from app.domains import signal_engine_cache as cache
from app.domains.signal_matrix import (
    DEFAULT_PINNED_INSTRUMENTS,
    config_for_instrument,
    instrument_key,
    merge_globals_row,
    pinned_instruments,
    row_metric_id,
    split_snapshot,
)


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    cache.reset_signal_cache_for_tests()
    yield
    cache.reset_signal_cache_for_tests()


def test_default_pinned_instruments() -> None:
    pinned = pinned_instruments(None)
    assert pinned == list(DEFAULT_PINNED_INSTRUMENTS)
    assert "NSE:NIFTY 50" in pinned
    assert "BSE:SENSEX" in pinned
    assert "NSE:NIFTY BANK" in pinned


def test_instrument_key_normalizes_spaces() -> None:
    assert instrument_key("NSE:NIFTY 50") == "NSE:NIFTY_50"
    assert instrument_key("BSE:SENSEX") == "BSE:SENSEX"


def test_row_metric_id_is_instrument_scoped() -> None:
    nifty = row_metric_id("levels", "NSE:NIFTY 50")
    sensex = row_metric_id("levels", "BSE:SENSEX")
    assert nifty == "levels:NSE:NIFTY_50"
    assert sensex == "levels:BSE:SENSEX"
    assert nifty != sensex
    assert row_metric_id("option_chain", None) == "option_chain"


def test_merge_keeps_row_gating_counts() -> None:
    """Do not recompute passed over every non-null metric (legacy vs matrix)."""
    globals_doc = {
        "engine_enabled": True,
        "metrics": [
            {"id": "dow", "category": "Global Markets Watch", "passed": True},
            {"id": "crude", "category": "Global Markets Watch", "passed": True},
        ],
    }
    row_doc = {
        "instrument": "NSE:NIFTY 50",
        "passed": 1,
        "evaluable": 4,
        "metrics": [
            {"id": "spot", "category": "Data & Charts Watch", "passed": True},
            {"id": "pcr", "category": "Options Watch", "passed": False},
        ],
    }
    merged = merge_globals_row(globals_doc, row_doc)
    assert merged is not None
    assert merged["passed"] == 1
    assert merged["evaluable"] == 4


def test_config_for_instrument_clears_manual_overrides() -> None:
    from app.domains.signal_engine import SignalEngineConfig

    primary = SignalEngineConfig(
        underlying_symbol="NSE:NIFTY 50",
        pcr=0.9,
        max_pain=24500.0,
        ivp=55.0,
        oi_pct_chg=1.2,
        iv_chg=-0.5,
        engine_enabled=True,
        mock=False,
    )
    row = config_for_instrument(primary, symbol="BSE:SENSEX", label="SENSEX")
    assert row.underlying_symbol == "BSE:SENSEX"
    assert row.pcr is None
    assert row.max_pain is None
    assert row.ivp is None
    assert row.oi_pct_chg is None
    assert row.iv_chg is None
    assert row.engine_enabled is True
    assert row.mock is False


def test_split_and_merge_snapshot() -> None:
    payload = {
        "engine_enabled": True,
        "mock": False,
        "has_broker": True,
        "underlying": {"symbol": "NSE:NIFTY 50", "label": "NIFTY 50"},
        "atm": 24500,
        "entry_ready": True,
        "entry": {"status": "ready", "label": "BUY"},
        "passed": 2,
        "evaluable": 3,
        "metrics": [
            {
                "id": "dow",
                "category": "Global Markets Watch",
                "passed": True,
                "value": -0.2,
            },
            {
                "id": "spot",
                "category": "Data & Charts Watch",
                "passed": True,
                "value": 24500,
            },
            {
                "id": "levels",
                "category": "Levels & Technicals",
                "passed": False,
                "value": 1,
            },
        ],
    }
    globals_doc, row_doc = split_snapshot(payload)
    assert globals_doc["engine_enabled"] is True
    assert len(globals_doc["metrics"]) == 1
    assert globals_doc["metrics"][0]["id"] == "dow"
    assert row_doc["instrument"] == "NSE:NIFTY 50"
    assert row_doc["atm"] == 24500
    assert {m["id"] for m in row_doc["metrics"]} == {"spot", "levels"}

    merged = merge_globals_row(globals_doc, row_doc)
    assert merged is not None
    assert merged["matrix"] is True
    assert merged["atm"] == 24500
    assert merged["engine_enabled"] is True
    assert len(merged["metrics"]) == 3
    assert merged["passed"] == 2
    assert merged["evaluable"] == 3


@pytest.mark.asyncio
async def test_merged_frame_falls_back_when_row_missing() -> None:
    """Globals-only dual-write must not hide a full snapshot's passed/entry."""
    payload = {
        "engine_enabled": True,
        "feed_source": "live",
        "passed": 4,
        "evaluable": 10,
        "computed_at_ms": 1_700_000_000_000,
        "metrics": [],
    }
    assert await cache.set_snapshot("tenant-fb", payload) is True
    # Dual-write created globals without a row (no underlying).
    assert await cache.get_globals("tenant-fb") is not None
    assert await cache.get_row("tenant-fb", "NSE:NIFTY 50") is None
    frame = await cache.merged_frame("tenant-fb")
    assert frame is not None
    assert frame["passed"] == 4
    assert frame.get("matrix") is not True


@pytest.mark.asyncio
async def test_set_snapshot_dual_writes_matrix() -> None:
    payload = {
        "engine_enabled": True,
        "feed_source": "live",
        "underlying": {"symbol": "NSE:NIFTY 50", "label": "NIFTY 50"},
        "metrics": [
            {"id": "dow", "category": "Global Markets Watch", "passed": True},
            {"id": "spot", "category": "Data & Charts Watch", "passed": True},
        ],
        "config_epoch": 0,
    }
    assert await cache.set_snapshot("tenant-m", payload) is True
    globals_doc = await cache.get_globals("tenant-m")
    row = await cache.get_row("tenant-m", "NSE:NIFTY 50")
    assert globals_doc is not None
    assert row is not None
    assert row["instrument"] == "NSE:NIFTY 50"

    sensex_row = {
        "instrument": "BSE:SENSEX",
        "underlying": {"symbol": "BSE:SENSEX", "label": "SENSEX"},
        "metrics": [
            {"id": "spot", "category": "Data & Charts Watch", "passed": False},
        ],
        "atm": 80000,
    }
    await cache.set_row("tenant-m", "BSE:SENSEX", sensex_row)
    nifty_frame = await cache.merged_frame("tenant-m", instrument="NSE:NIFTY 50")
    sensex_frame = await cache.merged_frame("tenant-m", instrument="BSE:SENSEX")
    assert nifty_frame is not None and sensex_frame is not None
    assert nifty_frame["underlying"]["symbol"] == "NSE:NIFTY 50"
    assert sensex_frame["underlying"]["symbol"] == "BSE:SENSEX"
    assert sensex_frame["atm"] == 80000
    # Globals shared across rows.
    assert nifty_frame.get("engine_enabled") is True
    assert sensex_frame.get("engine_enabled") is True


@pytest.mark.asyncio
async def test_invalidate_underlying_keeps_globals() -> None:
    await cache.set_snapshot(
        "tenant-m",
        {
            "feed_source": "live",
            "engine_enabled": True,
            "underlying": {"symbol": "NSE:NIFTY 50"},
            "metrics": [
                {"id": "dow", "category": "Global Markets Watch", "passed": True},
            ],
        },
    )
    assert (await cache.get_globals("tenant-m") or {}).get("engine_enabled") is True
    await cache.invalidate_underlying_dependent("tenant-m")
    globals_doc = await cache.get_globals("tenant-m")
    assert globals_doc is not None
    assert globals_doc.get("engine_enabled") is True
    assert await cache.get_row("tenant-m", "NSE:NIFTY 50") is None
    assert await cache.get_snapshot("tenant-m") is None


@pytest.mark.asyncio
async def test_touch_watcher_tracks_instrument() -> None:
    await cache.touch_watcher("tenant-m", instrument="NSE:NIFTY 50")
    await cache.touch_watcher("tenant-m", instrument="BSE:SENSEX")
    watched = set(await cache.list_watched_instruments("tenant-m"))
    assert instrument_key("NSE:NIFTY 50") in watched
    assert instrument_key("BSE:SENSEX") in watched


@pytest.mark.asyncio
async def test_schedule_matrix_refresh_skips_without_extra_watchers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uuid

    from app.domains import signal_engine_worker as worker

    worker.reset_matrix_bg_for_tests()
    tid = uuid.uuid4()
    tenant_key = str(tid)
    await cache.touch_watcher(tenant_key, instrument="NSE:NIFTY 50")

    opened = {"n": 0}

    async def boom_bg(*_a, **_k):
        opened["n"] += 1
        raise AssertionError("must not spawn matrix bg with only primary watcher")

    monkeypatch.setattr(worker, "_refresh_watched_matrix_rows_bg", boom_bg)
    task = await worker.schedule_watched_matrix_refresh(
        tid,
        auth_org_id="org",
        epoch=1,
        primary_underlying="NSE:NIFTY 50",
    )
    assert task is None
    assert opened["n"] == 0
    assert await cache.get_metric(tenant_key, "matrix_refresh_gate") is None


@pytest.mark.asyncio
async def test_schedule_matrix_refresh_gates_second_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import uuid

    from app.domains import signal_engine_worker as worker

    worker.reset_matrix_bg_for_tests()
    tid = uuid.uuid4()
    tenant_key = str(tid)
    await cache.touch_watcher(tenant_key, instrument="NSE:NIFTY 50")
    await cache.touch_watcher(tenant_key, instrument="BSE:SENSEX")

    started = asyncio.Event()
    release = asyncio.Event()
    runs = {"n": 0}

    async def slow_bg(*_a, **_k):
        runs["n"] += 1
        started.set()
        await release.wait()

    monkeypatch.setattr(worker, "_refresh_watched_matrix_rows_bg", slow_bg)

    first = await worker.schedule_watched_matrix_refresh(
        tid,
        auth_org_id="org",
        epoch=1,
        primary_underlying="NSE:NIFTY 50",
    )
    assert first is not None
    await started.wait()
    assert await cache.get_metric(tenant_key, "matrix_refresh_gate") is True

    second = await worker.schedule_watched_matrix_refresh(
        tid,
        auth_org_id="org",
        epoch=2,
        primary_underlying="NSE:NIFTY 50",
    )
    assert second is None
    assert runs["n"] == 1
    release.set()
    await first
    worker.reset_matrix_bg_for_tests()


@pytest.mark.asyncio
async def test_matrix_bg_peeks_before_session(monkeypatch: pytest.MonkeyPatch) -> None:
    import uuid

    from app.domains import signal_engine_worker as worker

    worker.reset_matrix_bg_for_tests()
    tid = uuid.uuid4()
    # No watchers at all.
    sessions = {"n": 0}

    class BoomSession:
        async def __aenter__(self):
            sessions["n"] += 1
            raise AssertionError("SessionFactory must not open")

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(worker, "SessionFactory", lambda: BoomSession())
    await worker._refresh_watched_matrix_rows_bg(
        tid,
        auth_org_id="org",
        epoch=1,
        primary_underlying="NSE:NIFTY 50",
    )
    assert sessions["n"] == 0
