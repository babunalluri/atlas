"""Signal matrix globals/row split + merge tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

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


def test_split_snapshot_keeps_quote_stale() -> None:
    payload = {
        "underlying": {"symbol": "NSE:NIFTY 50", "label": "NIFTY 50"},
        "quote_stale": True,
        "quote_reference": "previous_close",
        "ce_stale": True,
        "feed_source": "live",
        "metrics": [],
    }
    _globals_doc, row_doc = split_snapshot(payload)
    assert row_doc["quote_stale"] is True
    assert row_doc["quote_reference"] == "previous_close"
    merged = merge_globals_row({"metrics": []}, row_doc)
    assert merged is not None
    assert merged["quote_stale"] is True
    assert merged["quote_reference"] == "previous_close"
    assert merged.get("ce_stale") is True


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


def _desk_frame(symbol: str = "NSE:NIFTY 50") -> dict:
    """A monolithic primary snapshot, as ``merged_frame`` falls back to."""
    return {
        "engine_enabled": True,
        "underlying": {"symbol": symbol, "label": "NIFTY 50"},
        "instrument": symbol,
        "atm": 24_300,
        "entry": {"side": "CE"},
        "entry_ready": True,
        "passed": 7,
        "evaluable": 9,
        "feed_source": "kite",
        "computed_at_ms": 1_700_000_000_000,
        "metrics": [
            {"id": "pcr", "category": "Options Chain Check", "value": 0.8},
            {"id": "adr", "category": "Global Markets Watch", "value": 1.1},
        ],
    }


def test_warming_row_frame_renames_and_blanks_the_row() -> None:
    from app.domains.signal_matrix import warming_row_frame

    out = warming_row_frame(
        _desk_frame(), symbol="NSE:RELIANCE", label="RELIANCE"
    )

    # The board must name what was asked for, not the desk primary.
    assert out["underlying"] == {"symbol": "NSE:RELIANCE", "label": "RELIANCE"}
    assert out["instrument"] == "NSE:RELIANCE"
    # NIFTY's row values must not survive under a RELIANCE heading.
    assert "atm" not in out
    assert out["entry"] is None
    assert out["entry_ready"] is False
    assert out["passed"] == 0
    assert out["evaluable"] == 0
    assert out["feed_source"] == "starting"
    assert out["engine_computing"] is True
    # Shared globals are genuinely tenant-wide, so they stay; row metrics go.
    kept = [row["id"] for row in out["metrics"]]
    assert kept == ["adr"]


def test_warming_row_frame_keeps_global_top_level_fields() -> None:
    from app.domains.signal_matrix import warming_row_frame

    frame = {**_desk_frame(), "mock": True, "team_slug": "signals-ops"}
    out = warming_row_frame(frame, symbol="NSE:TCS")

    assert out["mock"] is True
    assert out["team_slug"] == "signals-ops"
    assert out["engine_enabled"] is True
    assert out["underlying"]["label"] == "NSE:TCS"


@pytest.mark.asyncio
async def test_stream_frame_warms_poisoned_row_with_wrong_underlying() -> None:
    """A row stamped for BANKNIFTY but painting NIFTY must warm, not pass through."""
    import uuid

    from app.domains.signal_engine import stream_frame_from_cache

    tenant_key = str(uuid.uuid4())
    await cache.set_metric(tenant_key, "engine_enabled", "medium", True)
    await cache.set_globals(
        tenant_key,
        {"metrics": [{"id": "fii", "category": "macro", "value": 1}]},
    )
    # Poison: instrument key says BANKNIFTY, board is still NIFTY 50.
    await cache.set_row(
        tenant_key,
        "NSE:NIFTY BANK",
        {
            "instrument": "NSE:NIFTY BANK",
            "underlying": {"symbol": "NSE:NIFTY 50", "label": "NIFTY 50"},
            "passed": 23,
            "evaluable": 43,
            "feed_source": "live",
            "metrics": [{"id": "atm", "category": "price", "value": 25000}],
        },
    )

    frame = await stream_frame_from_cache(tenant_key, instrument="NSE:NIFTY BANK")

    assert frame is not None
    assert frame["underlying"]["symbol"] == "NSE:NIFTY BANK"
    assert frame["passed"] == 0
    assert frame["feed_source"] == "starting"
    assert frame.get("engine_computing") is True


@pytest.mark.asyncio
async def test_compute_state_payload_passes_config_override_to_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Matrix rows must compute the asked instrument, not silently reload primary."""
    import uuid

    from app.db.models import Role
    from app.domains.signal_engine import (
        SignalEngineConfig,
        SignalEngineService,
        _compute_state_payload,
    )
    from app.tenancy.context import TenantContext

    tenant_id = uuid.uuid4()
    session = MagicMock()
    session.info = {"tenant_id": tenant_id}
    context = TenantContext(
        tenant_id=tenant_id,
        user_id="tester",
        role=Role.platform_admin,
        auth_org_id="org-test",
    )
    service = SignalEngineService(session, context)
    seen: dict[str, object] = {}

    async def fake_state(*, config: SignalEngineConfig | None = None) -> dict:
        seen["config"] = config
        assert config is not None
        return {
            "underlying": {
                "symbol": config.underlying_symbol,
                "label": config.underlying_label or config.underlying_symbol,
            },
            "instrument": config.underlying_symbol,
            "feed_source": "live",
            "passed": 1,
            "evaluable": 1,
            "metrics": [],
            "engine_enabled": True,
        }

    monkeypatch.setattr(service, "state", fake_state)
    row_cfg = SignalEngineConfig(
        engine_enabled=True,
        underlying_symbol="NSE:NIFTY BANK",
        underlying_label="NIFTY BANK",
    )
    out = await _compute_state_payload(service, config=row_cfg, last_good=None)
    assert seen["config"] is row_cfg
    assert out["underlying"]["symbol"] == "NSE:NIFTY BANK"


@pytest.mark.asyncio
async def test_matrix_refresh_skips_writing_mismatched_underlying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If compute still paints the primary, do not store it under row:{asked}."""
    from app.domains import signal_engine_worker as worker
    from app.domains.signal_engine import SignalEngineConfig

    worker.reset_matrix_bg_for_tests()
    tenant_key = "tenant-mismatch-row"
    written: list[tuple[str, dict]] = []

    async def fake_extras(*_a: object, **_k: object) -> list[SignalEngineConfig]:
        return [
            SignalEngineConfig(
                underlying_symbol="NSE:NIFTY BANK", engine_enabled=True
            )
        ]

    async def fake_compute(_service: object, *, config: object, last_good: object):
        # Simulate the historical bug: ignore config, paint primary.
        return {
            "underlying": {"symbol": "NSE:NIFTY 50", "label": "NIFTY 50"},
            "instrument": "NSE:NIFTY 50",
            "metrics": [],
            "passed": 9,
        }

    async def fake_set_row(tid: str, sym: str, doc: dict, **_k: object) -> None:
        written.append((sym, doc))

    monkeypatch.setattr(worker, "_matrix_extra_configs", fake_extras)
    monkeypatch.setattr(worker, "_compute_state_payload", fake_compute)
    monkeypatch.setattr(cache, "set_row", fake_set_row)

    class _Nested:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *_a: object) -> bool:
            return False

    class _Session:
        def begin_nested(self) -> _Nested:
            return _Nested()

    await worker._refresh_pinned_matrix_rows(
        service=object(),
        session=_Session(),
        tenant_key=tenant_key,
        primary=SignalEngineConfig(
            underlying_symbol="NSE:NIFTY 50", engine_enabled=True
        ),
        epoch=1,
    )
    assert written == []
    worker.reset_matrix_bg_for_tests()


@pytest.mark.asyncio
async def test_stream_frame_warms_instrument_without_a_row() -> None:
    """An instrument with no matrix row must not paint the primary's numbers."""
    import uuid

    from app.domains.signal_engine import stream_frame_from_cache

    tenant_key = str(uuid.uuid4())
    await cache.set_metric(tenant_key, "engine_enabled", "medium", True)
    await cache.set_snapshot(tenant_key, _desk_frame(), force=True)

    frame = await stream_frame_from_cache(tenant_key, instrument="NSE:RELIANCE")

    assert frame is not None
    assert frame["underlying"]["symbol"] == "NSE:RELIANCE"
    assert frame["passed"] == 0
    assert frame["feed_source"] == "starting"
    # Opening it must register the watcher so the worker builds the row.
    watched = await cache.watched_instrument_symbols(tenant_key)
    assert "NSE:RELIANCE" in watched.values()


@pytest.mark.asyncio
async def test_stream_frame_serves_the_primary_unchanged() -> None:
    import uuid

    from app.domains.signal_engine import stream_frame_from_cache

    tenant_key = str(uuid.uuid4())
    await cache.set_metric(tenant_key, "engine_enabled", "medium", True)
    await cache.set_snapshot(tenant_key, _desk_frame(), force=True)

    frame = await stream_frame_from_cache(tenant_key, instrument="NSE:NIFTY 50")

    assert frame is not None
    assert frame["passed"] == 7
    assert frame["feed_source"] == "kite"


def test_preset_lookup_covers_equities_not_just_indices() -> None:
    """An equity row built with the index step resolves the wrong ATM."""
    from app.domains.signal_engine import preset_label, preset_strike_step

    assert preset_label("NSE:NIFTY 50") == "NIFTY 50"
    assert preset_strike_step("NSE:NIFTY 50") == 50
    assert preset_label("NSE:RELIANCE") == "RELIANCE"
    assert preset_strike_step("NSE:RELIANCE") == 20
    assert preset_strike_step("NSE:ITC") == 5
    # Unknown names fall back to the caller's step rather than guessing.
    assert preset_strike_step("NSE:NOTLISTED") is None
    assert preset_label("NSE:NOTLISTED") == "NSE:NOTLISTED"


@pytest.mark.asyncio
async def test_matrix_pass_stops_on_budget_and_resumes_next_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow watch set must rotate, not recompute the same head forever."""
    from app.domains import signal_engine_worker as worker
    from app.domains.signal_engine import SignalEngineConfig

    worker.reset_matrix_bg_for_tests()
    symbols = ["NSE:A", "NSE:B", "NSE:C", "NSE:D"]

    async def fake_extras(*args: object, **kwargs: object) -> list[SignalEngineConfig]:
        # Mirrors the real builder's contract: rotate to resume after the row
        # the last pass finished on, BEFORE the row cap is applied.
        order = list(symbols)
        resume_after = kwargs.get("resume_after")
        if resume_after in order:
            cut = order.index(resume_after) + 1
            order = order[cut:] + order[:cut]
        return [
            SignalEngineConfig(underlying_symbol=s, engine_enabled=True)
            for s in order
        ]

    computed: list[str] = []
    clock = {"t": 0.0}

    async def fake_compute(_service: object, *, config: object, last_good: object):
        computed.append(config.underlying_symbol)
        # Each row "takes" 6s — two fit inside the 10s budget.
        clock["t"] += 6.0
        return {"underlying": {"symbol": config.underlying_symbol}, "metrics": []}

    monkeypatch.setattr(worker, "_matrix_extra_configs", fake_extras)
    monkeypatch.setattr(worker, "_compute_state_payload", fake_compute)
    monkeypatch.setattr(worker.time, "monotonic", lambda: clock["t"])

    class _Nested:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *_a: object) -> bool:
            return False

    class _Session:
        def begin_nested(self) -> _Nested:
            return _Nested()

    async def run_pass() -> None:
        await worker._refresh_pinned_matrix_rows(
            service=object(),
            session=_Session(),
            tenant_key="tenant-budget",
            primary=SignalEngineConfig(
                underlying_symbol="NSE:PRIMARY", engine_enabled=True
            ),
            epoch=1,
        )

    await run_pass()
    # Budget is 10s and each row costs 6s, so the pass stops early.
    assert computed == ["NSE:A", "NSE:B"], computed

    computed.clear()
    await run_pass()
    # Resumes after the row it finished on rather than restarting at A.
    assert computed[0] == "NSE:C", computed
    worker.reset_matrix_bg_for_tests()


def test_lab_refresh_concurrency_is_bounded() -> None:
    """Ten open Lab windows must not launch ten simultaneous chain scans."""
    from app.domains.options_lab import LAB_REFRESH_CONCURRENCY

    assert 1 <= LAB_REFRESH_CONCURRENCY <= 5


async def test_state_endpoint_never_answers_with_the_desk_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cold cache must not swap the primary's board in under another name.

    ``/state`` is what the panel calls on mount and on Refresh. Falling through
    to the unscoped ``state_for_stream`` when no row is cached yet reproduced
    the exact mislabelling the ``instrument`` parameter exists to prevent.
    """
    from app.api import signals as signals_api

    seeded: list[str | None] = []

    async def _cold(_tenant_id: str, *, instrument: str | None = None):
        return None

    async def _seed(_service, *, instrument: str | None = None):
        seeded.append(instrument)
        return {"instrument": instrument, "engine_computing": True}, False

    async def _primary(_service):
        raise AssertionError("must not fall back to the desk primary")

    monkeypatch.setattr(signals_api, "stream_frame_from_cache", _cold)
    monkeypatch.setattr(signals_api, "seed_stream_cold_frame", _seed)
    monkeypatch.setattr(signals_api, "state_for_stream", _primary)
    monkeypatch.setattr(signals_api, "SignalEngineService", lambda *_a, **_k: object())

    context = SimpleNamespace(tenant_id="t-1")
    out = await signals_api.get_signal_state(
        context=context, session=object(), instrument="NSE:RELIANCE"
    )

    assert seeded == ["NSE:RELIANCE"]
    assert out["instrument"] == "NSE:RELIANCE"


async def test_state_endpoint_without_an_instrument_uses_the_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import signals as signals_api

    async def _primary(_service):
        return {"instrument": "NSE:NIFTY 50"}

    monkeypatch.setattr(signals_api, "state_for_stream", _primary)
    monkeypatch.setattr(signals_api, "SignalEngineService", lambda *_a, **_k: object())

    out = await signals_api.get_signal_state(
        context=SimpleNamespace(tenant_id="t-1"), session=object(), instrument=None
    )
    assert out["instrument"] == "NSE:NIFTY 50"
