"""Background ticker: pre-computes signal snapshots for watched tenants."""

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
from app.domains import signal_engine_cache as cache
from app.domains.kite_ticker_hub import (
    SOURCE_SIGNAL,
    assemble_quotes_from_book,
    get_kite_ticker_hub,
    resolve_kite_credentials,
    token_map_from_quotes,
)
from app.domains.signal_engine import (
    SignalEngineService,
    _compute_state_payload,
    seed_engine_enabled_metric,
)
from app.domains.signal_engine_constants import (
    SIGNAL_ACTIVE_TICK_MS,
    SIGNAL_TICK_DEADLINE_SECONDS,
    STREAM_INTERVAL_MS,
    TICKER_IDLE_POLL_SECONDS,
)
from app.tenancy.context import TenantContext

logger = get_logger(__name__)


async def sync_kite_for_signal_tenant(
    tenant_id: uuid.UUID,
    *,
    auth_org_id: str,
    session: Any | None = None,
    context: TenantContext | None = None,
    engine: SignalEngineService | None = None,
    config: Any | None = None,
) -> bool:
    """Subscribe the shared Kite hub to this tenant's Signal Engine symbols.

    When ``session`` / ``engine`` / ``config`` are provided (shared tick path),
    reuses that checkout instead of opening a second SessionFactory.
    """
    if not get_settings().kite_ticker_enabled:
        return False

    async def _run(
        sess: Any,
        ctx: TenantContext,
        svc: SignalEngineService,
        cfg: Any,
    ) -> bool:
        symbols = [
            s
            for s in [
                cfg.underlying_symbol,
                cfg.nifty_fut_symbol,
                cfg.ce_symbol,
                cfg.pe_symbol,
                cfg.india_vix_symbol,
            ]
            if s
        ]
        if not symbols:
            return False
        creds = await resolve_kite_credentials(sess, ctx)
        if creds is None:
            return False
        quotes = (
            await assemble_quotes_from_book(
                str(tenant_id),
                symbols,
                require_all=False,
                require_alive=False,
            )
            or {}
        )
        token_map = token_map_from_quotes(quotes)
        have_syms = set(token_map.values())
        missing = [sym for sym in symbols if sym not in have_syms]
        if missing:
            quotes.update(
                await svc._fetch_quote(missing, prefer="get_ltp", timeout_s=8.0)
            )
            token_map = token_map_from_quotes(quotes)
        if not token_map:
            return False
        api_key, access_token = creds
        await get_kite_ticker_hub().sync_tenant(
            str(tenant_id),
            api_key=api_key,
            access_token=access_token,
            token_to_symbol=token_map,
            source=SOURCE_SIGNAL,
        )
        return True

    try:
        if session is not None and engine is not None and config is not None:
            ctx = context or TenantContext(
                tenant_id=tenant_id,
                user_id="kite-ticker-sync",
                role=Role.tenant_admin,
                auth_org_id=auth_org_id,
                principal_type="scheduler",
            )
            return await _run(session, ctx, engine, config)

        context = TenantContext(
            tenant_id=tenant_id,
            user_id="kite-ticker-sync",
            role=Role.tenant_admin,
            auth_org_id=auth_org_id,
            principal_type="scheduler",
        )
        async with SessionFactory() as own_session:
            async with own_session.begin():
                await apply_tenant_guc(own_session, tenant_id)
                own_engine = SignalEngineService(own_session, context)
                own_config = await own_engine._load_config()
                return await _run(own_session, context, own_engine, own_config)
    except Exception as exc:
        logger.warning(
            "kite_ticker_signal_sync_failed",
            tenant_id=str(tenant_id),
            error=str(exc),
        )
        return False


async def refresh_tier_b_for_tenant(tenant_id: uuid.UUID, *, auth_org_id: str) -> None:
    """Refresh crude/VIX/aux caches without gating the Tier A snapshot tick."""
    tenant_key = str(tenant_id)
    # Gate at medium TTL so book-first ticks are not stampeded by aux REST.
    if await cache.get_metric(tenant_key, "tier_b_refresh_gate") is not None:
        return
    await cache.set_metric(tenant_key, "tier_b_refresh_gate", "medium", True)
    context = TenantContext(
        tenant_id=tenant_id,
        user_id="signal-tier-b",
        role=Role.tenant_admin,
        auth_org_id=auth_org_id,
        principal_type="scheduler",
    )
    try:
        async with SessionFactory() as session:
            async with session.begin():
                await apply_tenant_guc(session, tenant_id)
                service = SignalEngineService(session, context)
                config = await service._load_config()
                await service.refresh_tier_b_context(config)
    except Exception as exc:
        logger.warning(
            "signal_tier_b_refresh_failed",
            tenant_id=tenant_key,
            error=str(exc),
        )


def should_preserve_computed_at_ms(payload: dict[str, Any]) -> bool:
    """True for keep-last-good timeout frames — do not fake a fresh stamp."""
    return (
        bool(payload.get("engine_computing"))
        and payload.get("feed_source") not in (None, "starting", "stopped")
        and payload.get("computed_at_ms") is not None
    )


async def refresh_tenant_snapshot(tenant_id: uuid.UUID, *, auth_org_id: str) -> bool:
    """Compute one signal snapshot for a tenant. Returns True when stored."""
    tenant_key = str(tenant_id)
    if not await cache.try_compute_lock(tenant_key):
        return False

    heartbeat = cache.start_compute_lock_heartbeat(tenant_key)
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from app.domains import param_chart_cache as pc_cache
        from app.domains.param_chart import ParamChartService

        ist = ZoneInfo("Asia/Kolkata")
        now_ist = datetime.now(ist)
        day_s = now_ist.date().isoformat()
        after_close = (now_ist.hour, now_ist.minute) >= (15, 30)
        # Peek only — SET NX stays inside persist so a double-acquire cannot
        # skip the real write. Avoids opening Param Chart work on ~99.9% of ticks.
        if after_close:
            metrics_due = await pc_cache.eod_finalize_due(tenant_key, day=day_s)
        else:
            metrics_due = await pc_cache.metrics_persist_due(tenant_key, day=day_s)

        context = TenantContext(
            tenant_id=tenant_id,
            user_id="signal-ticker",
            role=Role.tenant_admin,
            auth_org_id=auth_org_id,
            principal_type="scheduler",
        )
        # One SessionFactory + one GUC for kite sync, state(), auto-ATM, and
        # (rarely) Param Chart metrics persist.
        epoch_at_start = await cache.get_config_epoch(tenant_key)
        async with SessionFactory() as session:
            async with session.begin():
                await apply_tenant_guc(session, tenant_id)
                service = SignalEngineService(session, context)
                config = await service._load_config()
                await sync_kite_for_signal_tenant(
                    tenant_id,
                    auth_org_id=auth_org_id,
                    session=session,
                    context=context,
                    engine=service,
                    config=config,
                )
                prior = await cache.get_snapshot(tenant_key)
                last_good = (
                    prior
                    if isinstance(prior, dict)
                    and prior.get("feed_source")
                    not in (None, "starting", "stopped")
                    else None
                )
                payload = await _compute_state_payload(
                    service, config=config, last_good=last_good
                )
                if payload.get("feed_source") == "live":
                    await service.maybe_persist_auto_atm_symbols(payload)
                if metrics_due:
                    # SAVEPOINT so a Param Chart SQL abort cannot poison the
                    # outer tick txn (Postgres 25P02 → commit fails → no snapshot).
                    try:
                        async with session.begin_nested():
                            await ParamChartService(
                                session, context
                            ).persist_metrics_from_signal_snapshot(
                                force=False, snapshot=payload
                            )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "param_chart_metrics_from_signal_failed",
                            tenant_id=tenant_key,
                            error=str(exc)[:160],
                        )
        keep_last_good = should_preserve_computed_at_ms(payload)
        if not keep_last_good:
            payload = {**payload, "computed_at_ms": int(time.time() * 1000)}
        payload = {**payload, "config_epoch": epoch_at_start}
        wrote = await cache.set_snapshot(tenant_key, payload)
        if not wrote:
            logger.info(
                "signal_ticker_stale_config_drop",
                tenant_id=tenant_key,
                config_epoch=epoch_at_start,
            )
            return False
        await seed_engine_enabled_metric(
            tenant_key,
            bool(payload.get("engine_enabled", config.engine_enabled)),
        )
        asyncio.create_task(
            refresh_tier_b_for_tenant(tenant_id, auth_org_id=auth_org_id)
        )
        return True
    except Exception as exc:
        logger.warning("signal_ticker_refresh_failed", tenant_id=str(tenant_id), error=str(exc))
        return False
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        await cache.release_compute_lock(tenant_key)


class SignalEngineWorker:
    """Ticks watched tenants at ~8 Hz so SSE readers can serve Redis snapshots."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

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
        # SSE still streams at STREAM_INTERVAL_MS from Redis; this loop only
        # refreshes snapshots. Book-first Tier A uses SIGNAL_ACTIVE_TICK_MS.
        active_interval = max(STREAM_INTERVAL_MS, SIGNAL_ACTIVE_TICK_MS) / 1000
        while not self._stop.is_set():
            try:
                has_watchers = await asyncio.wait_for(
                    self.tick(),
                    timeout=SIGNAL_TICK_DEADLINE_SECONDS,
                )
            except TimeoutError:
                logger.warning(
                    "signal_ticker_tick_deadline",
                    deadline_s=SIGNAL_TICK_DEADLINE_SECONDS,
                )
                has_watchers = True
            except Exception as exc:
                logger.exception("signal_ticker_tick_failed", error=str(exc))
                has_watchers = False
            interval = active_interval if has_watchers else TICKER_IDLE_POLL_SECONDS
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except TimeoutError:
                pass

    async def tick(self) -> bool:
        tenant_ids = await cache.list_watched_tenant_ids()
        if not tenant_ids:
            return False

        parsed: list[uuid.UUID] = []
        for tenant_id in tenant_ids:
            try:
                parsed.append(uuid.UUID(tenant_id))
            except ValueError:
                logger.warning("signal_ticker_skip_invalid_tenant", tenant_id=tenant_id)
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

        refresh_tasks: list[asyncio.Task[bool]] = []
        for tid in parsed:
            tenant_key = str(tid)
            if tenant_key not in org_by_tenant:
                continue
            refresh_tasks.append(
                asyncio.create_task(
                    refresh_tenant_snapshot(tid, auth_org_id=org_by_tenant[tenant_key])
                )
            )
        if refresh_tasks:
            await asyncio.gather(*refresh_tasks)
        return True
