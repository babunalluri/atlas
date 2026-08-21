"""Background ticker: pre-computes signal snapshots for watched tenants."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid

from sqlalchemy import select

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.db.models import Role, Tenant
from app.db.session import SessionFactory, apply_tenant_guc
from app.domains import signal_engine_cache as cache
from app.domains.kite_ticker_hub import (
    get_kite_ticker_hub,
    resolve_kite_credentials,
    token_map_from_quotes,
)
from app.domains.signal_engine_constants import STREAM_INTERVAL_MS, TICKER_IDLE_POLL_SECONDS
from app.domains.signal_engine import SignalEngineService
from app.tenancy.context import TenantContext

logger = get_logger(__name__)


async def sync_kite_for_signal_tenant(
    tenant_id: uuid.UUID, *, auth_org_id: str
) -> bool:
    """Subscribe the shared Kite hub to this tenant's Signal Engine symbols."""
    if not get_settings().kite_ticker_enabled:
        return False
    context = TenantContext(
        tenant_id=tenant_id,
        user_id="kite-ticker-sync",
        role=Role.tenant_admin,
        auth_org_id=auth_org_id,
        principal_type="scheduler",
    )
    try:
        async with SessionFactory() as session:
            async with session.begin():
                await apply_tenant_guc(session, tenant_id)
                engine = SignalEngineService(session, context)
                config = await engine._load_config()
                symbols = [
                    s
                    for s in [
                        config.underlying_symbol,
                        config.nifty_fut_symbol,
                        config.ce_symbol,
                        config.pe_symbol,
                        config.india_vix_symbol,
                    ]
                    if s
                ]
                if not symbols:
                    return False
                creds = await resolve_kite_credentials(session, context)
                if creds is None:
                    return False
                quotes = await engine._fetch_quote(symbols, prefer="get_quote")
                token_map = token_map_from_quotes(quotes)
                if not token_map:
                    return False
                api_key, access_token = creds
                await get_kite_ticker_hub().sync_tenant(
                    str(tenant_id),
                    api_key=api_key,
                    access_token=access_token,
                    token_to_symbol=token_map,
                )
                return True
    except Exception as exc:
        logger.warning(
            "kite_ticker_signal_sync_failed",
            tenant_id=str(tenant_id),
            error=str(exc),
        )
        return False


async def refresh_tenant_snapshot(tenant_id: uuid.UUID, *, auth_org_id: str) -> bool:
    """Compute one signal snapshot for a tenant. Returns True when stored."""
    tenant_key = str(tenant_id)
    if not await cache.try_compute_lock(tenant_key):
        return False

    heartbeat = cache.start_compute_lock_heartbeat(tenant_key)
    try:
        # Prefer live WS quotes before the expensive state() fan-out.
        await sync_kite_for_signal_tenant(tenant_id, auth_org_id=auth_org_id)
        context = TenantContext(
            tenant_id=tenant_id,
            user_id="signal-ticker",
            role=Role.tenant_admin,
            auth_org_id=auth_org_id,
            principal_type="scheduler",
        )
        async with SessionFactory() as session:
            async with session.begin():
                await apply_tenant_guc(session, tenant_id)
                service = SignalEngineService(session, context)
                payload = await service.state()
        payload = {**payload, "computed_at_ms": int(time.time() * 1000)}
        await cache.set_snapshot(tenant_key, payload)
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
        active_interval = STREAM_INTERVAL_MS / 1000
        while not self._stop.is_set():
            try:
                has_watchers = await self.tick()
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
