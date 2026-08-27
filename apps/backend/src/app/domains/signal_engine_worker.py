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
    SignalEngineConfig,
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

# Strong refs so matrix / Tier-B background work is not GC'd mid-flight.
_BG_WORKER_TASKS: set[asyncio.Task[Any]] = set()
# At most one in-flight matrix row refresh per tenant (pileup guard).
_MATRIX_BG_BY_TENANT: dict[str, asyncio.Task[Any]] = {}


def _track_bg_worker(task: asyncio.Task[Any]) -> None:
    _BG_WORKER_TASKS.add(task)
    task.add_done_callback(_BG_WORKER_TASKS.discard)


def reset_matrix_bg_for_tests() -> None:
    _MATRIX_BG_BY_TENANT.clear()
    _BG_WORKER_TASKS.clear()


def _track_matrix_bg(tenant_key: str, task: asyncio.Task[Any]) -> None:
    _track_bg_worker(task)
    _MATRIX_BG_BY_TENANT[tenant_key] = task

    def _done(t: asyncio.Task[Any]) -> None:
        if _MATRIX_BG_BY_TENANT.get(tenant_key) is t:
            _MATRIX_BG_BY_TENANT.pop(tenant_key, None)

    task.add_done_callback(_done)


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

    Includes pinned / watched matrix underlyings (+ suggested FUTs) so row
    Tier-A reads hit the WS book instead of REST-stamping into 429s.

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
        from app.domains.options_lab import suggest_fut_symbol
        from app.domains.signal_matrix import instrument_key, pinned_instruments

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
        # Matrix rows: underlying + suggested FUT (~2 tokens/row; CE/PE land
        # after auto-ATM and are added on later syncs when present on rows).
        settings: dict[str, Any] = {}
        try:
            tool = await svc._signal_engine_tool()
            if tool is not None:
                settings = await svc._tool_settings(tool)
        except Exception:
            settings = {}
        pinned = pinned_instruments(settings)
        watched_keys = set(await cache.list_watched_instruments(str(tenant_id)))
        primary_key = instrument_key(cfg.underlying_symbol or "")
        for sym in pinned:
            if not sym or instrument_key(sym) == primary_key:
                continue
            symbols.append(sym)
            fut = suggest_fut_symbol(sym)
            if fut:
                symbols.append(fut)
        for key in watched_keys:
            if key == primary_key:
                continue
            match = next((s for s in pinned if instrument_key(s) == key), None)
            if match and match not in symbols:
                symbols.append(match)
                fut = suggest_fut_symbol(match)
                if fut and fut not in symbols:
                    symbols.append(fut)
        symbols = list(dict.fromkeys(s for s in symbols if s))
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


async def _matrix_extra_configs(
    service: SignalEngineService,
    primary: SignalEngineConfig,
    tenant_key: str,
    *,
    watched_only: bool,
) -> list[SignalEngineConfig]:
    """Build configs for non-primary matrix instruments that need warming."""
    from app.domains.signal_engine import UNDERLYING_PRESETS
    from app.domains.signal_matrix import (
        config_for_instrument,
        instrument_key,
        pinned_instruments,
    )

    if not primary.engine_enabled:
        return []

    settings: dict[str, Any] = {}
    try:
        tool = await service._signal_engine_tool()
        if tool is not None:
            settings = await service._tool_settings(tool)
    except Exception:
        settings = {}

    pinned = pinned_instruments(settings)
    watched_keys = set(await cache.list_watched_instruments(tenant_key))
    primary_sym = (primary.underlying_symbol or "").strip()
    primary_key = instrument_key(primary_sym)

    preset_by_symbol = {
        str(p.get("symbol") or ""): p for p in UNDERLYING_PRESETS if p.get("symbol")
    }

    targets: list[str] = []
    if watched_only:
        for key in watched_keys:
            if key == primary_key:
                continue
            match = next((s for s in pinned if instrument_key(s) == key), None)
            if match:
                targets.append(match)
    else:
        for sym in pinned:
            if instrument_key(sym) == primary_key:
                continue
            targets.append(sym)

    max_extra = 2 if primary_sym else 3
    out: list[SignalEngineConfig] = []
    for sym in targets[:max_extra]:
        preset = preset_by_symbol.get(sym) or {}
        out.append(
            config_for_instrument(
                primary,
                symbol=sym,
                label=str(preset.get("label") or sym),
                strike_step=preset.get("strike_step"),
            )
        )
    return out


async def refresh_tier_b_for_tenant(tenant_id: uuid.UUID, *, auth_org_id: str) -> None:
    """Refresh crude/VIX/aux caches without gating the Tier A snapshot tick."""
    tenant_key = str(tenant_id)
    # Gate slightly under chain TTL so PCR/IV refresh on the Phase-3 cadence.
    from app.domains.signal_engine_constants import TIER_B_REFRESH_GATE_MS

    if await cache.get_metric(tenant_key, "tier_b_refresh_gate") is not None:
        return
    await cache.set_metric(
        tenant_key,
        "tier_b_refresh_gate",
        "medium",
        True,
        ttl_ms=TIER_B_REFRESH_GATE_MS,
    )
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
                extras = await _matrix_extra_configs(
                    service, config, tenant_key, watched_only=True
                )
                await service.refresh_tier_b_context(
                    config, extra_row_configs=extras or None
                )
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
        payload: dict[str, Any] | None = None
        config: SignalEngineConfig | None = None
        async with SessionFactory() as session:
            try:
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
                    # After a preset switch, never keep the previous index's board
                    # as "last good" — that freezes STARTING / wrong CE/PE.
                    if isinstance(last_good, dict) and config.underlying_symbol:
                        prior_under = str(
                            (last_good.get("underlying") or {}).get("symbol") or ""
                        )
                        if prior_under and prior_under != config.underlying_symbol:
                            last_good = None
                    # SAVEPOINT around state(): wait_for cancel can abort PG mid-SQL;
                    # without a nested txn the outer commit fails and set_snapshot
                    # never runs → desk stuck on STARTING.
                    async with session.begin_nested():
                        payload = await _compute_state_payload(
                            service, config=config, last_good=last_good
                        )
                    if payload.get("feed_source") == "live":
                        try:
                            await service.maybe_persist_auto_atm_symbols(payload)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "signal_auto_atm_persist_failed",
                                tenant_id=tenant_key,
                                error=str(exc)[:160],
                            )
                    # Mid-tick Start / preset PATCHes bump config_epoch (and can
                    # leave the desk on STARTING forever if we keep stamping the
                    # pre-tick epoch). Re-base when the underlying still matches;
                    # drop only when the desk has moved to a different index.
                    from app.domains.signal_matrix import instrument_key

                    fresh_cfg = await service._load_config()
                    under = (
                        payload.get("underlying")
                        if isinstance(payload.get("underlying"), dict)
                        else {}
                    )
                    payload_under = str(
                        under.get("symbol")
                        or payload.get("instrument")
                        or config.underlying_symbol
                        or ""
                    ).strip()
                    current_under = str(fresh_cfg.underlying_symbol or "").strip()
                    if instrument_key(payload_under) != instrument_key(current_under):
                        logger.info(
                            "signal_ticker_underlying_changed_drop",
                            tenant_id=tenant_key,
                            computed=payload_under,
                            current=current_under,
                            config_epoch=epoch_at_start,
                        )
                        return False
                    epoch_at_start = await cache.get_config_epoch(tenant_key)
                    if metrics_due:
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
            except Exception as exc:
                logger.warning(
                    "signal_ticker_refresh_failed",
                    tenant_id=str(tenant_id),
                    error=str(exc),
                )
                # A wait_for cancel mid-SQL breaks the asyncpg connection —
                # savepoint RELEASE / txn COMMIT then fail. Invalidate so the
                # pool discards it instead of recycling a poisoned connection.
                with contextlib.suppress(Exception):
                    await session.invalidate()
                # Still publish a computed timeout/starting frame if we have one —
                # otherwise a poisoned commit leaves the desk on STARTING forever.
                if payload is None or config is None:
                    return False
        if payload is None or config is None:
            return False
        keep_last_good = should_preserve_computed_at_ms(payload)
        if not keep_last_good:
            payload = {**payload, "computed_at_ms": int(time.time() * 1000)}
        payload = {**payload, "config_epoch": epoch_at_start}
        wrote = await cache.set_snapshot(tenant_key, payload)
        if not wrote:
            # Last-chance unstick: a live frame for the current underlying must
            # replace a stuck STARTING board even if epoch raced again.
            existing = await cache.get_snapshot(tenant_key)
            if (
                payload.get("feed_source") == "live"
                and isinstance(existing, dict)
                and existing.get("feed_source") == "starting"
            ):
                payload = {
                    **payload,
                    "config_epoch": await cache.get_config_epoch(tenant_key),
                }
                wrote = await cache.set_snapshot(tenant_key, payload, force=True)
        if not wrote:
            logger.info(
                "signal_ticker_stale_config_drop",
                tenant_id=tenant_key,
                config_epoch=epoch_at_start,
            )
            return False
        # Warm watched matrix rows off the Tier-A compute lock / tick budget.
        await schedule_watched_matrix_refresh(
            tenant_id,
            auth_org_id=auth_org_id,
            epoch=epoch_at_start,
            primary_underlying=config.underlying_symbol,
        )
        await seed_engine_enabled_metric(
            tenant_key,
            bool(payload.get("engine_enabled", config.engine_enabled)),
        )
        task_b = asyncio.create_task(
            refresh_tier_b_for_tenant(tenant_id, auth_org_id=auth_org_id)
        )
        _track_bg_worker(task_b)
        return True
    except Exception as exc:
        logger.warning("signal_ticker_refresh_failed", tenant_id=str(tenant_id), error=str(exc))
        return False
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        await cache.release_compute_lock(tenant_key)


async def schedule_watched_matrix_refresh(
    tenant_id: uuid.UUID,
    *,
    auth_org_id: str,
    epoch: int,
    primary_underlying: str | None,
) -> asyncio.Task[Any] | None:
    """Schedule matrix row refresh with pileup guards (peek + NX + single-flight).

    Returns the background task when spawned, else None. Peek watchers before
    any SessionFactory open; gate + in-process map stop tick-cadence pileups.
    """
    from app.domains.signal_engine_constants import MATRIX_REFRESH_GATE_MS
    from app.domains.signal_matrix import instrument_key

    tenant_key = str(tenant_id)
    watched = await cache.list_watched_instruments(tenant_key)
    primary_key = instrument_key(primary_underlying or "")
    if not any(k != primary_key for k in watched):
        return None
    existing = _MATRIX_BG_BY_TENANT.get(tenant_key)
    if existing is not None and not existing.done():
        return None
    if await cache.get_metric(tenant_key, "matrix_refresh_gate") is not None:
        return None
    await cache.set_metric(
        tenant_key,
        "matrix_refresh_gate",
        "medium",
        True,
        ttl_ms=MATRIX_REFRESH_GATE_MS,
    )
    task = asyncio.create_task(
        _refresh_watched_matrix_rows_bg(
            tenant_id,
            auth_org_id=auth_org_id,
            epoch=epoch,
            primary_underlying=primary_underlying,
        )
    )
    _track_matrix_bg(tenant_key, task)
    return task


async def _refresh_watched_matrix_rows_bg(
    tenant_id: uuid.UUID,
    *,
    auth_org_id: str,
    epoch: int,
    primary_underlying: str | None = None,
) -> None:
    """Background: compute only watched non-primary matrix rows."""
    from app.domains.signal_matrix import instrument_key

    tenant_key = str(tenant_id)
    # Peek again before opening SessionFactory — zero extra watchers → no DB.
    watched = await cache.list_watched_instruments(tenant_key)
    primary_key = instrument_key(primary_underlying or "")
    if not any(k != primary_key for k in watched):
        return

    context = TenantContext(
        tenant_id=tenant_id,
        user_id="signal-matrix",
        role=Role.tenant_admin,
        auth_org_id=auth_org_id,
        principal_type="scheduler",
    )
    try:
        async with SessionFactory() as session:
            async with session.begin():
                await apply_tenant_guc(session, tenant_id)
                service = SignalEngineService(session, context)
                primary = await service._load_config()
                await _refresh_pinned_matrix_rows(
                    service=service,
                    session=session,
                    tenant_key=tenant_key,
                    primary=primary,
                    epoch=epoch,
                    watched_only=True,
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "signal_matrix_pinned_refresh_failed",
            tenant_id=tenant_key,
            error=str(exc)[:200],
        )


async def _refresh_pinned_matrix_rows(
    *,
    service: SignalEngineService,
    session: Any,
    tenant_key: str,
    primary: SignalEngineConfig,
    epoch: int,
    watched_only: bool = True,
    primary_payload: dict[str, Any] | None = None,
) -> None:
    """Compute non-primary matrix instruments into row:{instrument} keys.

    When ``watched_only`` (default), skip pinned symbols with no live SSE
    instrument watcher — avoids burning the tick budget on idle rows.
    """
    from app.domains.signal_matrix import split_snapshot

    extras = await _matrix_extra_configs(
        service, primary, tenant_key, watched_only=watched_only
    )
    for row_cfg in extras:
        sym = (row_cfg.underlying_symbol or "").strip()
        if not sym:
            continue
        async with session.begin_nested():
            row_payload = await _compute_state_payload(
                service, config=row_cfg, last_good=None
            )
        if not isinstance(row_payload, dict):
            continue
        row_payload = {
            **row_payload,
            "computed_at_ms": int(time.time() * 1000),
            "config_epoch": epoch,
            "instrument": sym,
        }
        _globals, row_doc = split_snapshot(row_payload)
        await cache.set_row(tenant_key, sym, row_doc)
        if _globals:
            existing = await cache.get_globals(tenant_key)
            if existing is None:
                await cache.set_globals(tenant_key, _globals)


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
