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
from app.domains.options_lab import OptionsLabConfig, chain_state_for_stream, mock_chain_snapshot
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
