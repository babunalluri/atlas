"""Asyncio Kite WebSocket quote hub — shared by Options Lab and Signal Engine.

Writes per-symbol quote rows into the broker metric cache so ``_fetch_quote``
can overlay live LTP/OI on REST snapshots. REST remains the source for IV and
any symbols the ticker book does not yet cover.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import struct
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from app.core.logging import get_logger
from app.domains import signal_engine_cache as cache

logger = get_logger(__name__)

KITE_WS_URL = "wss://ws.kite.trade"
QUOTE_SYM_PREFIX = "quote:sym:"
TICKER_ALIVE_KEY = "quote:ticker:alive"
# Full mode carries OI; quote mode (44 bytes) does not.
WS_MODE_FULL = "full"
WS_MODE_QUOTE = "quote"
TICKER_STALE_SECONDS = 5.0

_hub: "KiteTickerHub | None" = None


def get_kite_ticker_hub() -> "KiteTickerHub":
    global _hub
    if _hub is None:
        _hub = KiteTickerHub()
    return _hub


def quote_source_for_tenant(tenant_id: str) -> str:
    """Return ``ticker`` when the hub recently wrote ticks for this tenant."""
    hub = _hub
    if hub is None:
        return "rest"
    state = hub._tenants.get(tenant_id)
    if state is None or not state.connected:
        return "rest"
    if state.last_tick_at <= 0:
        return "rest"
    if (time.monotonic() - state.last_tick_at) > TICKER_STALE_SECONDS:
        return "rest"
    return "ticker"


def reset_kite_ticker_hub_for_tests() -> None:
    global _hub
    if _hub is not None:
        _hub._tenants.clear()
    _hub = None
    reset_kite_credential_cache_for_tests()


def _price_divisor(instrument_token: int) -> float:
    # CDS / BCD currency segments use 1e7; everything else uses paise (/100).
    segment = instrument_token & 0xFF
    if segment in {3, 6}:  # cds, bcd
        return 10_000_000.0
    return 100.0


def parse_binary_ticks(payload: bytes) -> list[dict[str, Any]]:
    """Parse one Kite binary WS frame into quote-shaped dicts (keyed by token)."""
    if not payload or len(payload) < 2:
        return []
    if len(payload) == 1:
        return []  # heartbeat

    offset = 0
    (packet_count,) = struct.unpack_from(">H", payload, offset)
    offset += 2
    ticks: list[dict[str, Any]] = []
    for _ in range(packet_count):
        if offset + 2 > len(payload):
            break
        (packet_len,) = struct.unpack_from(">H", payload, offset)
        offset += 2
        if packet_len <= 0 or offset + packet_len > len(payload):
            break
        packet = payload[offset : offset + packet_len]
        offset += packet_len
        parsed = _parse_quote_packet(packet)
        if parsed is not None:
            ticks.append(parsed)
    return ticks


def _parse_quote_packet(packet: bytes) -> dict[str, Any] | None:
    if len(packet) < 8:
        return None
    (token,) = struct.unpack_from(">i", packet, 0)
    divisor = _price_divisor(token)
    ltp = struct.unpack_from(">i", packet, 4)[0] / divisor

    row: dict[str, Any] = {
        "instrument_token": token,
        "last_price": ltp,
        "ltp": ltp,
    }

    # Index quote (28 bytes) vs instrument quote (44) vs full (184) vs ltp (8).
    if len(packet) == 8:
        return row

    if len(packet) in {28, 32}:
        if len(packet) >= 28:
            high = struct.unpack_from(">i", packet, 8)[0] / divisor
            low = struct.unpack_from(">i", packet, 12)[0] / divisor
            open_ = struct.unpack_from(">i", packet, 16)[0] / divisor
            close = struct.unpack_from(">i", packet, 20)[0] / divisor
            row["ohlc"] = {"open": open_, "high": high, "low": low, "close": close}
            row["net_change"] = struct.unpack_from(">i", packet, 24)[0] / divisor
        return row

    if len(packet) >= 44:
        row["last_traded_quantity"] = struct.unpack_from(">i", packet, 8)[0]
        row["average_traded_price"] = struct.unpack_from(">i", packet, 12)[0] / divisor
        row["volume"] = struct.unpack_from(">i", packet, 16)[0]
        row["buy_quantity"] = struct.unpack_from(">i", packet, 20)[0]
        row["sell_quantity"] = struct.unpack_from(">i", packet, 24)[0]
        open_ = struct.unpack_from(">i", packet, 28)[0] / divisor
        high = struct.unpack_from(">i", packet, 32)[0] / divisor
        low = struct.unpack_from(">i", packet, 36)[0] / divisor
        close = struct.unpack_from(">i", packet, 40)[0] / divisor
        row["ohlc"] = {"open": open_, "high": high, "low": low, "close": close}

    if len(packet) >= 52:
        row["open_interest"] = struct.unpack_from(">i", packet, 48)[0]
        row["oi"] = row["open_interest"]

    return row


async def write_ticker_rows(tenant_id: str, rows_by_symbol: dict[str, dict[str, Any]]) -> None:
    """Persist per-symbol ticker rows into the shared broker metric cache."""
    if not rows_by_symbol:
        return
    for symbol, row in rows_by_symbol.items():
        await cache.set_metric(tenant_id, f"{QUOTE_SYM_PREFIX}{symbol}", "broker", row)
    await cache.set_metric(
        tenant_id,
        TICKER_ALIVE_KEY,
        "broker",
        {"ts": int(time.time()), "count": len(rows_by_symbol)},
    )


async def assemble_quotes_from_book(
    tenant_id: str,
    symbols: list[str],
    *,
    require_all: bool = True,
) -> dict[str, Any] | None:
    """Build a quote map from ticker book.

    When ``require_all`` is True, returns None unless every symbol has a row.
    When False, returns whatever is present (may be empty dict).
    """
    if not symbols:
        return {}
    alive = await cache.get_metric(tenant_id, TICKER_ALIVE_KEY)
    if not isinstance(alive, dict):
        return None if require_all else {}
    merged: dict[str, Any] = {}
    for symbol in symbols:
        row = await cache.get_metric(tenant_id, f"{QUOTE_SYM_PREFIX}{symbol}")
        if not isinstance(row, dict):
            if require_all:
                return None
            continue
        merged[symbol] = row
    if require_all and len(merged) != len(symbols):
        return None
    return merged


def overlay_ticker_rows(
    base: dict[str, Any],
    ticker: dict[str, Any],
) -> dict[str, Any]:
    """Prefer live ticker LTP/OI/volume on top of REST rows (keeps IV/greeks).

    Promote anonymous ``_flat`` only when attribution is unambiguous (exactly one
    ticker symbol). Otherwise drop ``_flat`` so it cannot stick IV onto the wrong
    instrument when ticker has multiple keys.
    """
    if not ticker:
        return base
    out = dict(base)
    flat = out.get("_flat")
    ticker_symbols = [s for s in ticker if s != "_flat" and isinstance(ticker.get(s), dict)]
    if isinstance(flat, dict):
        if len(ticker_symbols) == 1:
            symbol = ticker_symbols[0]
            if symbol not in out or not isinstance(out.get(symbol), dict):
                promoted = dict(flat)
                if not promoted.get("symbol"):
                    promoted["symbol"] = symbol
                out[symbol] = promoted
        out.pop("_flat", None)

    for symbol, tick in ticker.items():
        if not isinstance(tick, dict) or symbol == "_flat":
            continue
        existing = out.get(symbol)
        if isinstance(existing, dict):
            merged = dict(existing)
            for key in (
                "last_price",
                "ltp",
                "open_interest",
                "oi",
                "volume",
                "ohlc",
                "instrument_token",
                "buy_quantity",
                "sell_quantity",
                "average_traded_price",
                "last_traded_quantity",
                "net_change",
            ):
                if key in tick and tick[key] is not None:
                    merged[key] = tick[key]
            out[symbol] = merged
        else:
            out[symbol] = dict(tick)
    return out


def _ws_mode_for_token(token: int) -> str:
    """Indices use quote packets; F&O needs full for OI."""
    segment = token & 0xFF
    # 9 = indices on NSE in Kite token packing (common); keep quote for small packets.
    if segment == 9:
        return WS_MODE_QUOTE
    return WS_MODE_FULL


@dataclass
class _TenantFeed:
    api_key: str
    access_token: str
    token_to_symbol: dict[int, str] = field(default_factory=dict)
    desired_tokens: set[int] = field(default_factory=set)
    pending_unsubscribe: set[int] = field(default_factory=set)
    connected: bool = False
    last_tick_at: float = 0.0
    task: asyncio.Task[None] | None = None
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    dirty: asyncio.Event = field(default_factory=asyncio.Event)
    credentials_epoch: int = 0


class KiteTickerHub:
    """One WebSocket connection per tenant API key; subscribe/unsubscribe live."""

    def __init__(self) -> None:
        self._tenants: dict[str, _TenantFeed] = {}
        self._supervisor: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._enabled = True

    def start(self) -> None:
        # Allow restart after stop() (tests / hot reload).
        self._stop = asyncio.Event()
        self._enabled = True
        if self._supervisor is None or self._supervisor.done():
            self._supervisor = asyncio.create_task(self._supervise())

    async def stop(self) -> None:
        self._enabled = False
        self._stop.set()
        for feed in list(self._tenants.values()):
            feed.stop.set()
            if feed.task is not None:
                feed.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await feed.task
        self._tenants.clear()
        if self._supervisor is not None:
            self._supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._supervisor
            self._supervisor = None

    async def sync_tenant(
        self,
        tenant_id: str,
        *,
        api_key: str,
        access_token: str,
        token_to_symbol: dict[int, str],
    ) -> None:
        """Replace desired subscriptions for a tenant and (re)start its feed."""
        if not self._enabled:
            return
        try:
            from app.core.settings import get_settings

            if not get_settings().kite_ticker_enabled:
                return
        except Exception:
            # Settings unavailable (rare) — refuse to open sockets blindly.
            return
        if not api_key or not access_token:
            return
        next_map = {
            int(tok): str(sym) for tok, sym in token_to_symbol.items() if tok and sym
        }
        feed = self._tenants.get(tenant_id)
        if feed is None:
            feed = _TenantFeed(api_key=api_key, access_token=access_token)
            self._tenants[tenant_id] = feed
        else:
            creds_changed = (
                feed.api_key != api_key or feed.access_token != access_token
            )
            feed.api_key = api_key
            feed.access_token = access_token
            if creds_changed and feed.task is not None and not feed.task.done():
                feed.credentials_epoch += 1
                feed.stop.set()
                feed.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await feed.task
                feed.stop = asyncio.Event()
                feed.task = None

        old_map = dict(feed.token_to_symbol)
        old_tokens = set(feed.desired_tokens)
        feed.token_to_symbol = next_map
        feed.desired_tokens = set(next_map.keys())
        removed = old_tokens - feed.desired_tokens
        feed.pending_unsubscribe |= removed
        map_changed = old_map != next_map or bool(removed)
        if map_changed:
            feed.dirty.set()
        if feed.task is None or feed.task.done():
            feed.stop = asyncio.Event()
            # Fresh connect always needs an initial subscribe.
            feed.dirty.set()
            feed.task = asyncio.create_task(self._run_tenant(tenant_id, feed))

    async def clear_tenant(self, tenant_id: str) -> None:
        feed = self._tenants.pop(tenant_id, None)
        if feed is None:
            return
        feed.stop.set()
        if feed.task is not None:
            feed.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await feed.task

    async def _supervise(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=2.0)
            except TimeoutError:
                pass

    async def _run_tenant(self, tenant_id: str, feed: _TenantFeed) -> None:
        backoff = 1.0
        while not feed.stop.is_set() and not self._stop.is_set():
            try:
                await self._session(tenant_id, feed)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                feed.connected = False
                logger.warning(
                    "kite_ticker_session_failed",
                    tenant_id=tenant_id,
                    error=str(exc),
                )
                try:
                    await asyncio.wait_for(feed.stop.wait(), timeout=backoff)
                except TimeoutError:
                    pass
                backoff = min(backoff * 2, 30.0)

    async def _session(self, tenant_id: str, feed: _TenantFeed) -> None:
        try:
            import websockets
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("websockets package is required for Kite ticker") from exc

        query = urlencode({"api_key": feed.api_key, "access_token": feed.access_token})
        url = f"{KITE_WS_URL}?{query}"
        async with websockets.connect(url, ping_interval=None, max_size=2**22) as ws:
            feed.connected = True
            feed.last_tick_at = 0.0
            await self._apply_subscriptions(ws, feed)
            while not feed.stop.is_set():
                recv = asyncio.create_task(ws.recv())
                dirty = asyncio.create_task(feed.dirty.wait())
                stop = asyncio.create_task(feed.stop.wait())
                done, pending = await asyncio.wait(
                    {recv, dirty, stop},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                if stop in done:
                    break
                # Handle dirty first, but do not drop a concurrent recv frame.
                if dirty in done:
                    feed.dirty.clear()
                    await self._apply_subscriptions(ws, feed)
                if recv in done:
                    try:
                        message = recv.result()
                    except Exception:
                        # Closed/failed socket must exit _session so _run_tenant
                        # can back off and reconnect (do not busy-loop).
                        feed.connected = False
                        raise
                    if isinstance(message, bytes):
                        ticks = parse_binary_ticks(message)
                        if not ticks:
                            continue
                        rows: dict[str, dict[str, Any]] = {}
                        for tick in ticks:
                            token = int(tick.get("instrument_token") or 0)
                            symbol = feed.token_to_symbol.get(token)
                            if not symbol:
                                continue
                            row = dict(tick)
                            row["symbol"] = symbol
                            rows[symbol] = row
                        if rows:
                            feed.last_tick_at = time.monotonic()
                            await write_ticker_rows(tenant_id, rows)

        feed.connected = False

    async def _apply_subscriptions(self, ws: Any, feed: _TenantFeed) -> None:
        if feed.pending_unsubscribe:
            gone = sorted(feed.pending_unsubscribe)
            feed.pending_unsubscribe.clear()
            await ws.send(json.dumps({"a": "unsubscribe", "v": gone}))
        tokens = sorted(feed.desired_tokens)
        if not tokens:
            return
        await ws.send(json.dumps({"a": "subscribe", "v": tokens}))
        by_mode: dict[str, list[int]] = {}
        for token in tokens:
            by_mode.setdefault(_ws_mode_for_token(token), []).append(token)
        for mode, mode_tokens in by_mode.items():
            await ws.send(json.dumps({"a": "mode", "v": [mode, mode_tokens]}))


_CRED_CACHE: dict[str, tuple[float, tuple[str, str]]] = {}
_CRED_CACHE_TTL_S = 30.0


def reset_kite_credential_cache_for_tests() -> None:
    _CRED_CACHE.clear()


async def resolve_kite_credentials(session: Any, context: Any) -> tuple[str, str] | None:
    """Load api_key + access_token from signals-ops broker toolkit binding."""
    from app.agent_runtime.factory import AgentFactoryService
    from app.domains.signal_engine import SignalEngineService, _tenant_key
    from app.tools.providers import merge_tenant_python_settings

    tenant_id = _tenant_key(context)
    cached = _CRED_CACHE.get(tenant_id)
    if cached is not None:
        expires_at, pair = cached
        if time.monotonic() < expires_at:
            return pair

    engine = SignalEngineService(session, context)
    factory = AgentFactoryService(session, context)
    async for _team, _version, binding, _source in engine._iter_signal_bindings():
        if binding.tool_definition_id is None:
            continue
        definition = await engine.tools.get(binding.tool_definition_id)
        if definition is None or definition.kind != "tenant_python":
            continue
        slug = (definition.slug or "").lower()
        if "signal" in slug:
            continue
        if definition.published_version_id is None:
            continue
        published = await engine.tool_versions.get(definition.published_version_id)
        if published is None:
            continue
        settings = dict(published.settings or {})
        credential_value: str | None = None
        if definition.credential_id is not None:
            credential = await factory.credentials.get(definition.credential_id)
            if credential is not None:
                credential_value = factory._decrypt(
                    credential.encrypted_value,
                    credential.key_version,
                )
        merged, _ = merge_tenant_python_settings(settings, credential_value)
        api_key = str(merged.get("api_key") or "").strip()
        access_token = str(merged.get("access_token") or merged.get("token") or "").strip()
        if api_key and access_token:
            pair = (api_key, access_token)
            _CRED_CACHE[tenant_id] = (time.monotonic() + _CRED_CACHE_TTL_S, pair)
            return pair
    return None


def token_map_from_quotes(quotes: dict[str, Any]) -> dict[int, str]:
    """Extract instrument_token → exchange:symbol from a quote map."""
    out: dict[int, str] = {}
    for key, row in quotes.items():
        if key == "_flat" or not isinstance(row, dict):
            continue
        raw = row.get("instrument_token")
        try:
            token = int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            token = 0
        if token <= 0:
            continue
        symbol = str(row.get("symbol") or key).strip()
        if not symbol:
            continue
        out[token] = symbol
    return out
