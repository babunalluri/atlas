"""Background ticker: pre-computes Options Lab chain snapshots for watched desks."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.models import Role, Tenant
from app.db.session import SessionFactory, apply_tenant_guc
from app.domains import options_lab_cache as ol_cache
from app.domains.kite_ticker_hub import (
    SOURCE_OPTIONS_LAB,
    get_kite_ticker_hub,
    resolve_kite_credentials,
    token_map_from_quotes,
)
from app.domains.options_lab import OptionsLabConfig, OptionsLabService, _clamp_wings
from app.domains.signal_engine import _tenant_key
from app.domains.signal_engine_constants import STREAM_INTERVAL_MS, TICKER_IDLE_POLL_SECONDS
from app.tenancy.context import TenantContext

logger = get_logger(__name__)


def _parse_tenant_ids(raw_ids: list[str]) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for tid in raw_ids:
        try:
            out.append(uuid.UUID(tid))
        except ValueError:
            logger.warning("options_lab_ticker_skip_invalid_tenant", tenant_id=tid)
    return out


async def refresh_options_lab_snapshot(
    tenant_id: uuid.UUID,
    *,
    auth_org_id: str,
    wings: int,
    underlying: str | None = None,
) -> bool:
    """Compute one Options Lab chain snapshot. Returns True when stored.

    ``underlying`` is the instrument the watching window pinned (E1/E2). The
    sentinel ``-`` — and None — mean "whatever the tenant desk config says".
    """
    from app.domains.options_lab import config_for_underlying
    from app.domains.options_lab_cache import DESK_DEFAULT_INSTRUMENT

    wings = _clamp_wings(wings)
    tenant_key = str(tenant_id)
    # The hub source key must match the slot the watcher registered, or the
    # prune below sees its own subscription as stale and drops it every tick.
    watch_slot = underlying
    if underlying == DESK_DEFAULT_INSTRUMENT:
        underlying = None
    context = TenantContext(
        tenant_id=tenant_id,
        user_id="options-lab-ticker",
        role=Role.tenant_admin,
        auth_org_id=auth_org_id,
        principal_type="scheduler",
    )
    try:
        async with SessionFactory() as session:
            async with session.begin():
                await apply_tenant_guc(session, tenant_id)
                service = OptionsLabService(session, context)
                config = config_for_underlying(await service._read_config(), underlying)
                fingerprint = config.cache_fingerprint()
                if not await ol_cache.try_compute_lock(
                    tenant_key, wings=wings, fingerprint=fingerprint
                ):
                    return False
                try:
                    payload = await service.chain_snapshot(wings=wings, config=config)
                    await ol_cache.set_snapshot(
                        tenant_key,
                        payload,
                        wings=wings,
                        fingerprint=fingerprint,
                        underlying=underlying,
                    )
                    if not config.mock:
                        await _sync_kite_from_chain_payload(
                            session,
                            context,
                            service,
                            config,
                            payload,
                            source_slot=watch_slot,
                        )
                    return True
                finally:
                    await ol_cache.release_compute_lock(
                        tenant_key, wings=wings, fingerprint=fingerprint
                    )
    except Exception as exc:
        logger.warning(
            "options_lab_ticker_refresh_failed",
            tenant_id=str(tenant_id),
            error=str(exc),
        )
        return False


def options_lab_source_key(underlying: str | None) -> str:
    """Hub source key for one Lab instrument (``options_lab:NSE:NIFTY 50``)."""
    return f"{SOURCE_OPTIONS_LAB}:{ol_cache.instrument_slug(underlying)}"


def stale_lab_source_keys(
    source_keys: Iterable[str], keep: set[str]
) -> list[str]:
    """Lab source keys to unsubscribe: ours, and no longer watched.

    Never touches Signal's or Param Chart's sources — they own their own keys
    on the same tenant feed.
    """
    prefix = f"{SOURCE_OPTIONS_LAB}:"
    return [
        key for key in source_keys if key.startswith(prefix) and key not in keep
    ]


def watched_lab_source_keys(
    watched: list[tuple[str, int, str | None]],
) -> dict[str, set[str]]:
    """``{tenant_id: {source_key, ...}}`` for the currently watched windows."""
    live: dict[str, set[str]] = {}
    for tenant_id, _wings, instrument in watched:
        live.setdefault(tenant_id, set()).add(options_lab_source_key(instrument))
    return live


async def _prune_options_lab_sources(
    watched: list[tuple[str, int, str | None]],
) -> None:
    """Drop hub sources for Lab instruments nobody is watching any more.

    Without this a closed window's chain stays subscribed for the life of the
    process, and ``feed.sources`` grows with every instrument ever opened.
    """
    hub = get_kite_ticker_hub()
    live = watched_lab_source_keys(watched)
    for tenant_id, feed in list(hub._tenants.items()):
        for key in stale_lab_source_keys(feed.sources, live.get(tenant_id, set())):
            await hub.drop_source(tenant_id, key)


async def _sync_kite_from_chain_payload(
    session: Any,
    context: TenantContext,
    service: OptionsLabService,
    config: OptionsLabConfig,
    payload: dict[str, Any],
    source_slot: str | None = None,
) -> None:
    """Subscribe using instrument tokens already present on the chain snapshot.

    ``source_slot`` is the watch slot this refresh belongs to (``-`` for a
    window that pinned no instrument). It keys the hub source so the prune can
    match subscriptions to watchers; without it a desk-default window
    subscribed under its resolved symbol and pruned itself on the next tick.
    """
    if config.mock or not config.underlying_symbol:
        return
    try:
        from app.core.settings import get_settings

        if not get_settings().kite_ticker_enabled:
            return
    except Exception:
        return
    creds = await resolve_kite_credentials(session, context)
    if creds is None:
        return
    api_key, access_token = creds
    token_map: dict[int, str] = {}
    for row in payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        for side in ("ce", "pe"):
            leg = row.get(side)
            if not isinstance(leg, dict):
                continue
            symbol = str(leg.get("symbol") or "").strip()
            raw = leg.get("instrument_token")
            if not symbol:
                continue
            try:
                token = int(raw) if raw is not None else 0
            except (TypeError, ValueError):
                token = 0
            if token > 0:
                token_map[token] = symbol
    # Seed spot/fut only when this tenant feed does not already know them.
    seed_symbols = [
        s for s in [config.underlying_symbol, config.fut_symbol] if s
    ]
    if seed_symbols:
        hub = get_kite_ticker_hub()
        feed = hub._tenants.get(_tenant_key(context))
        known = set(feed.token_to_symbol.values()) if feed is not None else set()
        known |= set(token_map.values())
        need_seed = [sym for sym in seed_symbols if sym not in known]
        if need_seed:
            seed_quotes = await service.engine._fetch_quote(
                need_seed, prefer="get_quote"
            )
            token_map.update(token_map_from_quotes(seed_quotes))
    if not token_map:
        return
    # Per-instrument source key. The hub REPLACES a source's whole token map,
    # so a single "options_lab" key meant two Lab windows on different
    # underlyings overwrote each other every tick — NIFTY's chain unsubscribed
    # for SENSEX's, then back — and both fell back to REST.
    await get_kite_ticker_hub().sync_tenant(
        _tenant_key(context),
        api_key=api_key,
        access_token=access_token,
        token_to_symbol=token_map,
        source=options_lab_source_key(
            source_slot if source_slot is not None else config.underlying_symbol
        ),
    )


class OptionsLabWorker:
    """Ticks watched Options Lab desks at ~8 Hz so SSE readers serve snapshots."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._idle_clear_ticks = 0

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        active_interval = STREAM_INTERVAL_MS / 1000
        while not self._stop.is_set():
            try:
                has_watchers = await self.tick()
            except Exception as exc:
                logger.exception("options_lab_ticker_tick_failed", error=str(exc))
                has_watchers = False
            interval = active_interval if has_watchers else TICKER_IDLE_POLL_SECONDS
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except TimeoutError:
                pass

    async def tick(self) -> bool:
        watched = await ol_cache.list_watched()
        signal_synced = await _sync_signal_engine_watchers()

        if not watched:
            if signal_synced:
                # Signal desk still owns hub subscriptions — do not accumulate
                # toward a clear, and never wipe feeds on a transient sync miss.
                self._idle_clear_ticks = 0
                return True
            self._idle_clear_ticks += 1
            if self._idle_clear_ticks >= 3:
                # Drop idle WS feeds after a few empty polls with no Signal watchers.
                hub = get_kite_ticker_hub()
                for tenant_id in list(hub._tenants.keys()):
                    await hub.clear_tenant(tenant_id)
                self._idle_clear_ticks = 0
            return False

        self._idle_clear_ticks = 0
        await _prune_options_lab_sources(watched)
        org_by_tenant: dict[str, str] = {}
        parsed = _parse_tenant_ids([tid for tid, _wings, _instrument in watched])
        if not parsed:
            return signal_synced
        async with SessionFactory() as session:
            rows = (
                await session.execute(
                    select(Tenant.id, Tenant.auth_org_id).where(
                        Tenant.id.in_(parsed),
                        Tenant.is_active.is_(True),
                    )
                )
            ).all()
            for tenant_id, auth_org_id in rows:
                org_by_tenant[str(tenant_id)] = auth_org_id

        # When Signal Engine is also live for a tenant, shrink Options Lab
        # wings so sandbox quote storms cannot starve Signal compute.
        from app.domains import signal_engine_cache as signal_cache
        from app.domains.options_lab import MIN_WINGS

        signal_watched = set(await signal_cache.list_watched_tenant_ids())

        # Bound the fan-out: each refresh is a full chain scan, and the tick
        # used to launch one per watcher at once.
        from app.domains.options_lab import LAB_REFRESH_CONCURRENCY

        gate = asyncio.Semaphore(LAB_REFRESH_CONCURRENCY)

        async def _refresh_gated(
            tid: uuid.UUID, *, auth_org_id: str, wings: int, underlying: str | None
        ) -> bool:
            async with gate:
                return await refresh_options_lab_snapshot(
                    tid,
                    auth_org_id=auth_org_id,
                    wings=wings,
                    underlying=underlying,
                )

        refresh_tasks: list[asyncio.Task[bool]] = []
        for tenant_id, wings, instrument in watched:
            if tenant_id not in org_by_tenant:
                continue
            try:
                tid = uuid.UUID(tenant_id)
            except ValueError:
                continue
            effective_wings = wings
            if tenant_id in signal_watched:
                effective_wings = min(int(wings), MIN_WINGS)
                logger.info(
                    "options_lab_yield_to_signal",
                    tenant_id=tenant_id,
                    wings=wings,
                    effective_wings=effective_wings,
                )
            refresh_tasks.append(
                asyncio.create_task(
                    _refresh_gated(
                        tid,
                        auth_org_id=org_by_tenant[tenant_id],
                        wings=effective_wings,
                        underlying=instrument,
                    )
                )
            )
        if refresh_tasks:
            # return_exceptions: one instrument's failure must not cancel the
            # rest of the tick's refreshes.
            await asyncio.gather(*refresh_tasks, return_exceptions=True)
        return True


async def _sync_signal_engine_watchers() -> bool:
    from app.domains import signal_engine_cache as signal_cache
    from app.domains.signal_engine_worker import sync_kite_for_signal_tenant

    tenant_ids = await signal_cache.list_watched_tenant_ids()
    if not tenant_ids:
        return False
    parsed = _parse_tenant_ids(tenant_ids)
    if not parsed:
        return False
    org_by_tenant: dict[str, str] = {}
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                select(Tenant.id, Tenant.auth_org_id).where(
                    Tenant.id.in_(parsed),
                    Tenant.is_active.is_(True),
                )
            )
        ).all()
        for tenant_id, auth_org_id in rows:
            org_by_tenant[str(tenant_id)] = auth_org_id

    synced_any = False
    for tenant_id in tenant_ids:
        auth_org_id = org_by_tenant.get(tenant_id)
        if not auth_org_id:
            continue
        try:
            tid = uuid.UUID(tenant_id)
        except ValueError:
            continue
        if await sync_kite_for_signal_tenant(tid, auth_org_id=auth_org_id):
            synced_any = True
    return synced_any
