"""Background ticker: Param Chart overlay + SOURCE_PARAM_CHART kite subscribe."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from typing import Any

from sqlalchemy import select

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.db.models import Role, Tenant
from app.db.session import SessionFactory, apply_tenant_guc
from app.domains import param_chart_cache as pc_cache
from app.domains.kite_ticker_hub import (
    SOURCE_PARAM_CHART,
    assemble_quotes_from_book,
    get_kite_ticker_hub,
    resolve_kite_credentials,
    token_map_from_quotes,
)
from app.domains.param_chart import (
    ParamChartConfig,
    ParamChartService,
    config_from_setup_cache,
    heal_option_symbols_for_month,
    param_chart_watch_symbols,
    refresh_overlay_from_cache,
)
from app.domains.signal_engine_constants import (
    STREAM_INTERVAL_MS,
    TICKER_IDLE_POLL_SECONDS,
    TIER_A_REST_GAP_FILL_MS,
)
from app.tenancy.context import TenantContext

logger = get_logger(__name__)

_REST_GAP_MS: dict[str, float] = {}
_KITE_SYNC_AT: dict[str, tuple[float, tuple[str, ...]]] = {}
KITE_SYNC_MIN_S = 2.0


def _parse_tenant_ids(raw_ids: list[str]) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for tid in raw_ids:
        try:
            out.append(uuid.UUID(tid))
        except ValueError:
            logger.warning("param_chart_ticker_skip_invalid_tenant", tenant_id=tid)
    return out


def _rest_gap_ok(tenant_id: str) -> bool:
    now = time.monotonic() * 1000
    last = _REST_GAP_MS.get(tenant_id, 0.0)
    if now - last < TIER_A_REST_GAP_FILL_MS:
        return False
    _REST_GAP_MS[tenant_id] = now
    return True


def _kite_sync_due(tenant_id: str, symbols: tuple[str, ...]) -> bool:
    now = time.monotonic()
    prev = _KITE_SYNC_AT.get(tenant_id)
    if prev is not None and prev[1] == symbols and (now - prev[0]) < KITE_SYNC_MIN_S:
        return False
    _KITE_SYNC_AT[tenant_id] = (now, symbols)
    return True


def reset_param_chart_worker_gates_for_tests() -> None:
    _REST_GAP_MS.clear()
    _KITE_SYNC_AT.clear()


async def refresh_param_chart_overlay(
    tenant_id: str, underlying: str | None = None
) -> bool:
    """Paint today's overlay from book + Redis. Returns True when stored.

    ``underlying`` selects the slot: overlays are per instrument, so a window
    scoped to SENSEX needs its own refresh rather than riding the tenant desk
    slot — otherwise only the SSE fall-through would ever repaint it.
    """
    try:
        frame = await refresh_overlay_from_cache(tenant_id, underlying=underlying)
        return frame is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "param_chart_overlay_refresh_failed",
            tenant_id=tenant_id,
            underlying=underlying or "-",
            error=str(exc)[:200],
        )
        return False


async def _watch_symbols_for_instruments(
    tenant_key: str,
    cfg: ParamChartConfig,
    underlyings: tuple[str | None, ...] = (),
) -> list[str]:
    """Union of under / FUT / CE / PE across every watched instrument.

    Resolves an ATM strike for scoped instruments first. Without it a scoped
    config has no strike, so ``heal_option_symbols_for_month`` yields no CE/PE
    and the option legs would never be subscribed. Spot comes from the book, so
    the first pass subscribes the underlying and the next one adds its legs.
    """
    from app.domains.param_chart import config_for_underlying, resolve_atm_strike

    wanted: tuple[str | None, ...] = underlyings or (None,)
    out: list[str] = []
    for underlying in wanted:
        scoped = cfg
        if underlying:
            scoped = await resolve_atm_strike(
                tenant_key, config_for_underlying(cfg, underlying)
            )
        y, m = scoped.resolved_year_month()
        scoped = heal_option_symbols_for_month(scoped, year=y, month=m)
        for sym in param_chart_watch_symbols(scoped):
            if sym and sym not in out:
                out.append(sym)
    return out


async def sync_kite_for_param_chart_tenant(
    tenant_id: uuid.UUID,
    *,
    auth_org_id: str,
    cfg: ParamChartConfig | None = None,
    underlyings: tuple[str | None, ...] = (),
) -> bool:
    """Subscribe under/FUT/CE/PE on the shared hub as SOURCE_PARAM_CHART only.

    DB txn is credentials + config only. Token REST is first-party Kite
    (no sandbox) and runs after the session is released.
    """
    if not get_settings().kite_ticker_enabled:
        return False
    tenant_key = str(tenant_id)
    if not await pc_cache.watcher_alive(tenant_key):
        await _clear_param_chart_source(tenant_key)
        return False

    context = TenantContext(
        tenant_id=tenant_id,
        user_id="param-chart-ticker",
        role=Role.tenant_admin,
        auth_org_id=auth_org_id,
        principal_type="scheduler",
    )
    creds: tuple[str, str] | None = None
    try:
        async with SessionFactory() as session:
            async with session.begin():
                await apply_tenant_guc(session, tenant_id)
                if cfg is None:
                    cfg = await config_from_setup_cache(tenant_key)
                    if cfg is None:
                        cfg = await ParamChartService(session, context)._read_config()
                creds = await resolve_kite_credentials(session, context)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "param_chart_kite_sync_failed",
            tenant_id=tenant_key,
            error=str(exc)[:200],
        )
        return False

    if cfg is None or creds is None:
        return False
    symbols = await _watch_symbols_for_instruments(tenant_key, cfg, underlyings)
    if not symbols:
        await _clear_param_chart_source(tenant_key)
        return False

    try:
        quotes = (
            await assemble_quotes_from_book(
                tenant_key,
                symbols,
                require_all=False,
                require_alive=False,
            )
            or {}
        )
        token_map = token_map_from_quotes(quotes)
        have = set(token_map.values())
        missing = [sym for sym in symbols if sym not in have]
        if missing:
            from app.domains import param_chart_token_store as token_store

            still: list[str] = []
            for sym in missing:
                tok = await token_store.get_instrument_token(sym)
                if tok:
                    token_map[int(tok)] = sym
                else:
                    still.append(sym)
            missing = still
        if missing and _rest_gap_ok(tenant_key):
            from app.domains.kite_rest import fetch_kite_quotes

            filled = await fetch_kite_quotes(
                api_key=creds[0],
                access_token=creds[1],
                symbols=missing,
                prefer="get_ltp",
                timeout_s=8.0,
            )
            if filled:
                quotes.update(filled)
                token_map = token_map_from_quotes(quotes)
                try:
                    from app.domains import param_chart_token_store as token_store

                    for sym in missing:
                        row = (
                            quotes.get(sym)
                            if isinstance(quotes.get(sym), dict)
                            else None
                        )
                        raw = (row or {}).get("instrument_token")
                        try:
                            tok = int(raw) if raw is not None else 0
                        except (TypeError, ValueError):
                            tok = 0
                        if tok > 0:
                            await token_store.put_instrument_token(sym, tok)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "param_chart_token_persist_failed",
                        tenant_id=tenant_key,
                        error=str(exc)[:160],
                    )
        if not token_map:
            return False
        api_key, access_token = creds
        await get_kite_ticker_hub().sync_tenant(
            tenant_key,
            api_key=api_key,
            access_token=access_token,
            token_to_symbol=token_map,
            source=SOURCE_PARAM_CHART,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "param_chart_kite_sync_failed",
            tenant_id=tenant_key,
            error=str(exc)[:200],
        )
        return False


async def _clear_param_chart_source(tenant_id: str) -> None:
    """Drop this tab's hub tokens without wiping Signal / Options Lab sources."""
    hub = get_kite_ticker_hub()
    feed = hub._tenants.get(tenant_id)
    if feed is None:
        return
    if SOURCE_PARAM_CHART not in feed.sources:
        return
    await hub.sync_tenant(
        tenant_id,
        api_key=feed.api_key,
        access_token=feed.access_token,
        token_to_symbol={},
        source=SOURCE_PARAM_CHART,
    )
    _KITE_SYNC_AT.pop(tenant_id, None)


class ParamChartWorker:
    """Ticks watched Param Chart desks: overlay at ~8 Hz, kite sync ~2 s."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._synced: set[str] = set()
        self._sync_inflight: set[str] = set()
        self._sync_tasks: set[asyncio.Task[Any]] = set()

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        for task in list(self._sync_tasks):
            task.cancel()
        if self._sync_tasks:
            await asyncio.gather(*self._sync_tasks, return_exceptions=True)
        self._sync_tasks.clear()
        self._sync_inflight.clear()
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
                logger.exception("param_chart_ticker_tick_failed", error=str(exc))
                has_watchers = False
            interval = active_interval if has_watchers else TICKER_IDLE_POLL_SECONDS
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except TimeoutError:
                pass

    def _spawn_kite_sync(
        self,
        tenant_id: uuid.UUID,
        *,
        tenant_key: str,
        auth_org_id: str,
        cfg: ParamChartConfig | None,
        underlyings: tuple[str | None, ...] = (),
    ) -> None:
        if tenant_key in self._sync_inflight:
            return
        self._sync_inflight.add(tenant_key)

        async def _run() -> None:
            try:
                await sync_kite_for_param_chart_tenant(
                    tenant_id,
                    auth_org_id=auth_org_id,
                    cfg=cfg,
                    underlyings=underlyings,
                )
            finally:
                self._sync_inflight.discard(tenant_key)

        task = asyncio.create_task(_run())
        self._sync_tasks.add(task)
        task.add_done_callback(self._sync_tasks.discard)

    async def tick(self) -> bool:
        # Per (tenant, instrument): each open Chart window has its own overlay
        # slot, and each needs repainting on the worker cadence.
        watched_pairs = await pc_cache.list_watched()
        watched = list(dict.fromkeys(tenant for tenant, _ in watched_pairs))
        watched_set = set(watched)
        for stale_id in list(self._synced):
            if stale_id not in watched_set:
                await _clear_param_chart_source(stale_id)
                self._synced.discard(stale_id)

        if not watched:
            return False

        overlay_tasks = [
            asyncio.create_task(refresh_param_chart_overlay(tenant_key, underlying))
            for tenant_key, underlying in watched_pairs
        ]
        if overlay_tasks:
            await asyncio.gather(*overlay_tasks, return_exceptions=True)

        if not get_settings().kite_ticker_enabled:
            return True

        # Which instruments each tenant is actually watching — the sync must
        # cover all of them, not just the desk config's.
        instruments_by_tenant: dict[str, tuple[str | None, ...]] = {}
        for tenant_key, underlying in watched_pairs:
            current = instruments_by_tenant.get(tenant_key, ())
            if underlying not in current:
                instruments_by_tenant[tenant_key] = (*current, underlying)

        due_keys: list[str] = []
        cfg_by_tenant: dict[str, ParamChartConfig | None] = {}
        for tenant_key in watched:
            if tenant_key in self._sync_inflight:
                self._synced.add(tenant_key)
                continue
            cfg = await config_from_setup_cache(tenant_key)
            symbols = (
                tuple(
                    await _watch_symbols_for_instruments(
                        tenant_key, cfg, instruments_by_tenant.get(tenant_key, ())
                    )
                )
                if cfg is not None
                else ()
            )
            if not _kite_sync_due(tenant_key, symbols):
                self._synced.add(tenant_key)
                continue
            due_keys.append(tenant_key)
            cfg_by_tenant[tenant_key] = cfg

        if not due_keys:
            return True

        parsed = _parse_tenant_ids(due_keys)
        if not parsed:
            return True

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
            for tenant_uuid, auth_org_id in rows:
                org_by_tenant[str(tenant_uuid)] = auth_org_id

        for tenant_key, auth_org_id in org_by_tenant.items():
            try:
                tenant_uuid = uuid.UUID(tenant_key)
            except ValueError:
                continue
            self._spawn_kite_sync(
                tenant_uuid,
                tenant_key=tenant_key,
                auth_org_id=auth_org_id,
                cfg=cfg_by_tenant.get(tenant_key),
                underlyings=instruments_by_tenant.get(tenant_key, ()),
            )
            self._synced.add(tenant_key)
        return True
