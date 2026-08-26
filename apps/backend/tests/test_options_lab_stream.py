"""Options Lab SSE stream + coalesce + Kite binary tick parser tests."""

from __future__ import annotations

import asyncio
import contextlib
import struct

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, Tenant
from app.domains import options_lab_cache as ol_cache
from app.domains import signal_engine_cache as signal_cache
from app.domains.kite_ticker_hub import (
    assemble_quotes_from_book,
    parse_binary_ticks,
    quote_source_for_tenant,
    reset_kite_ticker_hub_for_tests,
    write_ticker_rows,
)
from app.domains.options_lab import (
    OptionsLabConfig,
    chain_frame_from_cache,
    chain_state_for_stream,
    mock_chain_snapshot,
)
from app.domains.signal_engine_constants import STREAM_INTERVAL_MS
from app.main import app
from app.tenancy.context import TenantContext


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    ol_cache.reset_options_lab_cache_for_tests()
    signal_cache.reset_signal_cache_for_tests()
    reset_kite_ticker_hub_for_tests()
    yield
    ol_cache.reset_options_lab_cache_for_tests()
    signal_cache.reset_signal_cache_for_tests()
    reset_kite_ticker_hub_for_tests()


@pytest.fixture
async def options_lab_db(monkeypatch):
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    def make_session():
        return factory()

    for target in (
        "app.db.session.SessionFactory",
        "app.api.options_lab.SessionFactory",
        "app.auth.dependencies.SessionFactory",
    ):
        monkeypatch.setattr(target, make_session)

    yield factory
    await eng.dispose()


def _dev_headers(context: TenantContext) -> dict[str, str]:
    return {
        "x-dev-tenant-id": str(context.tenant_id),
        "x-dev-user-id": context.user_id,
        "x-dev-role": context.role.value,
    }


def test_parse_ltp_packet() -> None:
    token = 256265  # NSE index-ish token
    ltp_paise = 24500_00
    packet = struct.pack(">ii", token, ltp_paise)
    frame = struct.pack(">HH", 1, len(packet)) + packet
    ticks = parse_binary_ticks(frame)
    assert len(ticks) == 1
    assert ticks[0]["instrument_token"] == token
    assert ticks[0]["last_price"] == 24500.0


def test_parse_quote_packet_with_oi() -> None:
    token = 123456
    # 52-byte packet: quote fields through OI
    values = [
        token,
        100_00,  # ltp
        10,  # ltq
        99_00,  # avg
        1000,  # volume
        50,  # buy qty
        60,  # sell qty
        90_00,  # open
        110_00,  # high
        85_00,  # low
        95_00,  # close
        0,  # last traded timestamp
        7777,  # oi
    ]
    packet = struct.pack(">" + "i" * len(values), *values)
    assert len(packet) == 52
    frame = struct.pack(">HH", 1, len(packet)) + packet
    ticks = parse_binary_ticks(frame)
    assert ticks[0]["last_price"] == 100.0
    assert ticks[0]["volume"] == 1000
    assert ticks[0]["open_interest"] == 7777
    assert ticks[0]["ohlc"]["high"] == 110.0


@pytest.mark.asyncio
async def test_assemble_quotes_from_ticker_book() -> None:
    tenant = "tenant-ticker"
    await write_ticker_rows(
        tenant,
        {
            "NSE:NIFTY 50": {"last_price": 24500.0, "instrument_token": 1},
            "NFO:NIFTY26AUGFUT": {"last_price": 24510.0, "instrument_token": 2},
        },
    )
    assembled = await assemble_quotes_from_book(
        tenant, ["NSE:NIFTY 50", "NFO:NIFTY26AUGFUT"]
    )
    assert assembled is not None
    assert assembled["NSE:NIFTY 50"]["last_price"] == 24500.0
    missing = await assemble_quotes_from_book(tenant, ["NSE:NIFTY 50", "NFO:MISSING"])
    assert missing is None


@pytest.mark.asyncio
async def test_chain_state_for_stream_coalesce() -> None:
    tenant = "tenant-ol-stream"
    config = OptionsLabConfig(
        underlying_symbol="NSE:NIFTY 50",
        fut_symbol="NFO:NIFTY26AUGFUT",
        strike_step=50,
        mock=True,
    )
    wings = 5
    payload = mock_chain_snapshot(config, wings=wings)
    await ol_cache.set_snapshot(
        tenant, payload, wings=wings, fingerprint=config.cache_fingerprint()
    )

    class _StubService:
        context = type("Ctx", (), {"tenant_id": tenant})()

        async def _read_config(self):
            return config

        async def chain_snapshot(self, *, wings: int = 15):
            raise AssertionError("should not recompute when snapshot is warm")

    out = await chain_state_for_stream(_StubService(), wings=wings)  # type: ignore[arg-type]
    assert out["ok"] is True
    assert out["stream"] is True
    assert out["poll_ms"] == STREAM_INTERVAL_MS
    assert out["quote_source"] in {"rest", "ticker"}
    assert quote_source_for_tenant(tenant) == "rest"


@pytest.mark.asyncio
async def test_chain_frame_from_cache_fast_path() -> None:
    tenant = "tenant-ol-fast"
    config = OptionsLabConfig(
        underlying_symbol="NSE:NIFTY 50",
        fut_symbol="NFO:NIFTY26SEPFUT",
        strike_step=50,
        mock=True,
    )
    wings = 5
    payload = mock_chain_snapshot(config, wings=wings)
    await ol_cache.set_snapshot(
        tenant, payload, wings=wings, fingerprint=config.cache_fingerprint()
    )
    frame = await chain_frame_from_cache(tenant, wings=wings)
    assert frame is not None
    assert frame["ok"] is True
    assert frame["stream"] is True
    assert frame["poll_ms"] == STREAM_INTERVAL_MS
    watched = await ol_cache.list_watched()
    assert (tenant, wings) in watched


@pytest.mark.asyncio
async def test_chain_frame_from_cache_miss_without_fingerprint() -> None:
    tenant = "tenant-ol-miss"
    config = OptionsLabConfig(mock=True)
    wings = 5
    # Write snapshot without going through set_snapshot's remember path.
    bucket_payload = mock_chain_snapshot(config, wings=wings)
    # Direct in-process snapshot only (no fingerprint pointer).
    from app.domains.options_lab_cache import _snap_bucket_key, _snapshots, _now_ms
    from app.domains.signal_engine_constants import SNAPSHOT_TTL_MS

    key = _snap_bucket_key(
        tenant, wings=wings, fingerprint=config.cache_fingerprint()
    )
    _snapshots[key] = (_now_ms() + SNAPSHOT_TTL_MS, bucket_payload)
    assert await ol_cache.get_fingerprint(tenant, wings=wings) is None
    assert await chain_frame_from_cache(tenant, wings=wings) is None


@pytest.mark.asyncio
async def test_set_snapshot_remembers_fingerprint() -> None:
    tenant = "tenant-ol-fp"
    config = OptionsLabConfig(mock=True, fut_symbol="NFO:NIFTY26SEPFUT")
    wings = 8
    await ol_cache.set_snapshot(
        tenant,
        mock_chain_snapshot(config, wings=wings),
        wings=wings,
        fingerprint=config.cache_fingerprint(),
    )
    assert (
        await ol_cache.get_fingerprint(tenant, wings=wings)
        == config.cache_fingerprint()
    )


@pytest.mark.asyncio
async def test_clear_fingerprints_on_config_change_pointer() -> None:
    tenant = "tenant-ol-clear"
    config = OptionsLabConfig(mock=True)
    wings = 5
    await ol_cache.remember_fingerprint(
        tenant, wings=wings, fingerprint=config.cache_fingerprint()
    )
    await ol_cache.clear_fingerprints(tenant)
    assert await ol_cache.get_fingerprint(tenant, wings=wings) is None


@pytest.mark.asyncio
async def test_options_lab_stream_requires_auth(client) -> None:
    denied = await client.get("/admin/options-lab/stream")
    assert denied.status_code == 401


@pytest.mark.asyncio
async def test_fetch_quote_overlays_ticker_on_rest(monkeypatch) -> None:
    from app.domains.signal_engine import SignalEngineService

    tenant = "tenant-fetch"
    symbols = ["NSE:NIFTY 50"]
    await write_ticker_rows(
        tenant,
        {
            "NSE:NIFTY 50": {
                "last_price": 24123.0,
                "open_interest": 11,
                "instrument_token": 99,
            }
        },
    )

    class _Ctx:
        tenant_id = tenant

    service = SignalEngineService.__new__(SignalEngineService)
    service.context = _Ctx()  # type: ignore[assignment]

    async def _fake_tools():
        async def get_quote(**_kwargs):
            return {
                "ok": True,
                "data": {
                    "NSE:NIFTY 50": {
                        "last_price": 24000.0,
                        "implied_volatility": 12.5,
                        "instrument_token": 99,
                    }
                },
            }

        get_quote.__name__ = "get_quote"
        return [get_quote]

    monkeypatch.setattr(service, "_quote_tools", _fake_tools)
    out = await SignalEngineService._fetch_quote(service, symbols, prefer="get_quote")
    assert out["NSE:NIFTY 50"]["last_price"] == 24123.0  # ticker wins LTP
    assert out["NSE:NIFTY 50"]["implied_volatility"] == 12.5  # REST keeps IV
    assert out["NSE:NIFTY 50"]["open_interest"] == 11


@pytest.mark.asyncio
async def test_fetch_quote_reuses_rest_seeded_book(monkeypatch) -> None:
    """Screener batch seeds per-symbol book; single-symbol get_quote must reuse it."""
    from app.domains.kite_ticker_hub import write_rest_quote_book
    from app.domains.signal_engine import SignalEngineService

    tenant = "tenant-book-reuse"
    symbols = ["NSE:NIFTY 50"]
    await write_rest_quote_book(
        tenant,
        {
            "NSE:NIFTY 50": {
                "last_price": 24163.65,
                "ohlc": {"open": 24100.0, "high": 24200.0, "low": 24050.0, "close": 24150.0},
                "instrument_token": 256265,
            }
        },
    )

    class _Ctx:
        tenant_id = tenant

    service = SignalEngineService.__new__(SignalEngineService)
    service.context = _Ctx()  # type: ignore[assignment]

    async def _boom_tools():
        raise AssertionError("sandbox must not be called when REST book has ohlc")

    monkeypatch.setattr(service, "_quote_tools", _boom_tools)
    out = await SignalEngineService._fetch_quote(service, symbols, prefer="get_quote")
    assert out["NSE:NIFTY 50"]["last_price"] == 24163.65


@pytest.mark.asyncio
async def test_overlay_ticker_rows_prefers_live_ltp() -> None:
    from app.domains.kite_ticker_hub import overlay_ticker_rows

    base = {"NFO:X": {"last_price": 100.0, "implied_volatility": 15.0}}
    tick = {"NFO:X": {"last_price": 101.5, "open_interest": 9}}
    out = overlay_ticker_rows(base, tick)
    assert out["NFO:X"]["last_price"] == 101.5
    assert out["NFO:X"]["implied_volatility"] == 15.0
    assert out["NFO:X"]["open_interest"] == 9


@pytest.mark.asyncio
async def test_overlay_promotes_flat_rest_row() -> None:
    from app.domains.kite_ticker_hub import overlay_ticker_rows
    from app.domains.signal_engine import _find_quote_row

    base = {"_flat": {"last_price": 100.0, "implied_volatility": 14.0}}
    tick = {"NSE:NIFTY 50": {"last_price": 101.0, "open_interest": 5}}
    out = overlay_ticker_rows(base, tick)
    assert "_flat" not in out
    row = _find_quote_row(out, "NSE:NIFTY 50")
    assert row is not None
    assert row["last_price"] == 101.0
    assert row["implied_volatility"] == 14.0


@pytest.mark.asyncio
async def test_overlay_drops_ambiguous_flat() -> None:
    from app.domains.kite_ticker_hub import overlay_ticker_rows

    base = {"_flat": {"last_price": 100.0, "implied_volatility": 14.0}}
    tick = {
        "NSE:NIFTY 50": {"last_price": 101.0},
        "NFO:NIFTY26AUGFUT": {"last_price": 102.0},
    }
    out = overlay_ticker_rows(base, tick)
    assert "_flat" not in out
    assert out["NSE:NIFTY 50"].get("implied_volatility") is None
    assert out["NFO:NIFTY26AUGFUT"].get("implied_volatility") is None


@pytest.mark.asyncio
async def test_session_raises_on_closed_recv_instead_of_spinning() -> None:
    from app.domains.kite_ticker_hub import KiteTickerHub, _TenantFeed

    hub = KiteTickerHub()
    feed = _TenantFeed(api_key="k", access_token="t")
    feed.desired_tokens = {1}
    feed.token_to_symbol = {1: "NSE:NIFTY 50"}

    class _ClosedWS:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def send(self, *_a, **_k):
            return None

        async def recv(self):
            raise ConnectionError("closed")

    class _WSMod:
        def connect(self, *_a, **_k):
            return _ClosedWS()

    import sys

    sys.modules["websockets"] = _WSMod()  # type: ignore[assignment]
    try:
        with pytest.raises(ConnectionError):
            await hub._session("tenant", feed)
        assert feed.connected is False
    finally:
        sys.modules.pop("websockets", None)


@pytest.mark.asyncio
async def test_sync_tenant_skips_dirty_when_map_unchanged(monkeypatch) -> None:
    from app.domains.kite_ticker_hub import get_kite_ticker_hub

    class _Settings:
        kite_ticker_enabled = True

    monkeypatch.setattr(
        "app.core.settings.get_settings", lambda: _Settings()
    )
    hub = get_kite_ticker_hub()
    hub._enabled = True
    token_map = {101: "NFO:A", 102: "NFO:B"}
    await hub.sync_tenant(
        "t1", api_key="k", access_token="tok", token_to_symbol=token_map
    )
    feed = hub._tenants["t1"]
    # Cancel the connection task so tests don't open sockets.
    if feed.task is not None:
        feed.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await feed.task
        feed.task = asyncio.create_task(asyncio.sleep(3600))
    feed.dirty.clear()
    await hub.sync_tenant(
        "t1", api_key="k", access_token="tok", token_to_symbol=token_map
    )
    assert not feed.dirty.is_set()
    feed.task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await feed.task


@pytest.mark.asyncio
async def test_sync_tenant_merges_sources_without_dropping_other_desk(
    monkeypatch,
) -> None:
    from app.domains.kite_ticker_hub import (
        SOURCE_OPTIONS_LAB,
        SOURCE_SIGNAL,
        get_kite_ticker_hub,
    )

    class _Settings:
        kite_ticker_enabled = True

    monkeypatch.setattr("app.core.settings.get_settings", lambda: _Settings())
    hub = get_kite_ticker_hub()
    hub._enabled = True
    await hub.sync_tenant(
        "t-merge",
        api_key="k",
        access_token="tok",
        token_to_symbol={1: "NSE:NIFTY 50", 2: "NFO:NIFTYFUT"},
        source=SOURCE_SIGNAL,
    )
    feed = hub._tenants["t-merge"]
    if feed.task is not None:
        feed.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await feed.task
        feed.task = asyncio.create_task(asyncio.sleep(3600))
    feed.dirty.clear()
    feed.pending_unsubscribe.clear()

    await hub.sync_tenant(
        "t-merge",
        api_key="k",
        access_token="tok",
        token_to_symbol={3: "NFO:OPT1", 4: "NFO:OPT2"},
        source=SOURCE_OPTIONS_LAB,
    )
    assert feed.desired_tokens == {1, 2, 3, 4}
    assert feed.token_to_symbol[1] == "NSE:NIFTY 50"
    assert 1 not in feed.pending_unsubscribe

    # Signal shrinks — Options Lab tokens must remain subscribed.
    feed.dirty.clear()
    feed.pending_unsubscribe.clear()
    await hub.sync_tenant(
        "t-merge",
        api_key="k",
        access_token="tok",
        token_to_symbol={1: "NSE:NIFTY 50"},
        source=SOURCE_SIGNAL,
    )
    assert feed.desired_tokens == {1, 3, 4}
    assert feed.pending_unsubscribe == {2}
    feed.task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await feed.task


@pytest.mark.asyncio
async def test_sync_tenant_noop_when_web_concurrency_gt_1(monkeypatch) -> None:
    from app.domains.kite_ticker_hub import get_kite_ticker_hub

    class _Settings:
        kite_ticker_enabled = True

    monkeypatch.setattr("app.core.settings.get_settings", lambda: _Settings())
    monkeypatch.setattr(
        "app.domains.runtime.web_concurrency", lambda: 2
    )
    hub = get_kite_ticker_hub()
    hub._enabled = True
    await hub.sync_tenant(
        "t-multi",
        api_key="k",
        access_token="tok",
        token_to_symbol={1: "NSE:NIFTY 50"},
    )
    assert "t-multi" not in hub._tenants


@pytest.mark.asyncio
async def test_clean_ws_close_is_detected() -> None:
    from app.domains.kite_ticker_hub import _is_clean_ws_close

    class ConnectionClosedOK(Exception):
        pass

    class ConnectionClosed(Exception):
        def __init__(self, code: int):
            self.code = code
            super().__init__(f"code={code}")

    assert _is_clean_ws_close(ConnectionClosedOK("bye"))
    assert _is_clean_ws_close(ConnectionClosed(1000))
    assert not _is_clean_ws_close(ConnectionClosed(1006))
    assert not _is_clean_ws_close(RuntimeError("auth failed"))


@pytest.mark.asyncio
async def test_sync_tenant_respects_kite_ticker_disabled(monkeypatch) -> None:
    from app.domains.kite_ticker_hub import get_kite_ticker_hub

    class _Settings:
        kite_ticker_enabled = False

    monkeypatch.setattr(
        "app.core.settings.get_settings", lambda: _Settings()
    )
    hub = get_kite_ticker_hub()
    hub._enabled = True
    await hub.sync_tenant(
        "t-disabled",
        api_key="k",
        access_token="tok",
        token_to_symbol={1: "NSE:NIFTY 50"},
    )
    assert "t-disabled" not in hub._tenants


@pytest.mark.asyncio
async def test_hub_restart_after_stop() -> None:
    from app.domains.kite_ticker_hub import get_kite_ticker_hub

    hub = get_kite_ticker_hub()
    hub.start()
    await hub.stop()
    assert hub._stop.is_set()
    hub.start()
    assert not hub._stop.is_set()
    assert hub._enabled is True
    await hub.stop()


@pytest.mark.asyncio
async def test_options_lab_stream_denies_end_user(options_lab_db, tenant_a) -> None:
    async with options_lab_db() as session:
        session.add(
            Tenant(
                id=tenant_a.tenant_id,
                auth_org_id=tenant_a.auth_org_id,
                slug="acme",
                name="Acme Corp",
                branding={},
            )
        )
        await session.commit()

    headers = {
        "x-dev-tenant-id": str(tenant_a.tenant_id),
        "x-dev-user-id": tenant_a.user_id,
        "x-dev-role": "end_user",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.get("/admin/options-lab/stream", headers=headers)
        assert denied.status_code == 403


@pytest.mark.asyncio
async def test_options_lab_idle_does_not_clear_when_signal_synced(monkeypatch) -> None:
    from app.domains.kite_ticker_hub import get_kite_ticker_hub
    from app.domains.options_lab_worker import OptionsLabWorker

    hub = get_kite_ticker_hub()
    hub._tenants["keep-me"] = type("F", (), {"stop": asyncio.Event(), "task": None})()
    cleared: list[str] = []

    async def _clear(tenant_id: str) -> None:
        cleared.append(tenant_id)

    async def _async_empty():
        return []

    async def _async_true():
        return True

    monkeypatch.setattr(hub, "clear_tenant", _clear)
    monkeypatch.setattr(
        "app.domains.options_lab_worker.ol_cache.list_watched",
        _async_empty,
    )
    monkeypatch.setattr(
        "app.domains.options_lab_worker._sync_signal_engine_watchers",
        _async_true,
    )

    worker = OptionsLabWorker()
    for _ in range(5):
        assert await worker.tick() is True
    assert worker._idle_clear_ticks == 0
    assert cleared == []
    hub._tenants.pop("keep-me", None)


@pytest.mark.asyncio
async def test_options_lab_idle_resets_counter_after_transient_signal_fail(
    monkeypatch,
) -> None:
    """A brief signal_synced=False must not leave the counter primed to wipe."""
    from app.domains.kite_ticker_hub import get_kite_ticker_hub
    from app.domains.options_lab_worker import OptionsLabWorker

    hub = get_kite_ticker_hub()
    hub._tenants["keep-me"] = type("F", (), {"stop": asyncio.Event(), "task": None})()
    cleared: list[str] = []
    sync_results = iter([False, True, True])

    async def _clear(tenant_id: str) -> None:
        cleared.append(tenant_id)

    async def _async_empty():
        return []

    async def _sync():
        return next(sync_results)

    monkeypatch.setattr(hub, "clear_tenant", _clear)
    monkeypatch.setattr(
        "app.domains.options_lab_worker.ol_cache.list_watched",
        _async_empty,
    )
    monkeypatch.setattr(
        "app.domains.options_lab_worker._sync_signal_engine_watchers",
        _sync,
    )

    worker = OptionsLabWorker()
    assert await worker.tick() is False
    assert worker._idle_clear_ticks == 1
    assert await worker.tick() is True
    assert worker._idle_clear_ticks == 0
    assert cleared == []
    hub._tenants.pop("keep-me", None)


@pytest.mark.asyncio
async def test_options_lab_idle_wipes_hub_after_sustained_idle(monkeypatch) -> None:
    from app.domains.kite_ticker_hub import get_kite_ticker_hub
    from app.domains.options_lab_worker import OptionsLabWorker

    hub = get_kite_ticker_hub()
    hub._tenants["gone"] = type("F", (), {"stop": asyncio.Event(), "task": None})()
    cleared: list[str] = []

    async def _clear(tenant_id: str) -> None:
        cleared.append(tenant_id)
        hub._tenants.pop(tenant_id, None)

    async def _async_empty():
        return []

    async def _async_false():
        return False

    monkeypatch.setattr(hub, "clear_tenant", _clear)
    monkeypatch.setattr(
        "app.domains.options_lab_worker.ol_cache.list_watched",
        _async_empty,
    )
    monkeypatch.setattr(
        "app.domains.options_lab_worker._sync_signal_engine_watchers",
        _async_false,
    )

    worker = OptionsLabWorker()
    assert await worker.tick() is False
    assert await worker.tick() is False
    assert cleared == []
    assert await worker.tick() is False
    assert cleared == ["gone"]
    assert worker._idle_clear_ticks == 0


def test_price_divisor_cds_vs_bcd() -> None:
    from app.domains.kite_ticker_hub import _price_divisor

    assert _price_divisor(3) == 10_000_000.0  # CDS
    assert _price_divisor(6) == 10_000.0  # BCD
    assert _price_divisor(0x103) == 10_000_000.0
    assert _price_divisor(0x106) == 10_000.0
    assert _price_divisor(256 + 1) == 100.0  # NSE equity
