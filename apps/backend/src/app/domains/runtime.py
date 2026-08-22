"""Desk/domain background services — owned outside Agent OS.

Agent OS lifespan should only call ``start_domain_services`` /
``stop_domain_services``. Add ORD (or any new desk worker) here, not in
``agent_runtime/agent_os.py``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.core.settings import Settings, get_settings
from app.domains.kite_ticker_hub import KiteTickerHub, get_kite_ticker_hub
from app.domains.options_lab_bots_worker import OptionsLabBotsWorker
from app.domains.options_lab_worker import OptionsLabWorker
from app.domains.signal_engine_worker import SignalEngineWorker

logger = get_logger(__name__)


def web_concurrency() -> int:
    """Gunicorn/uvicorn worker count from WEB_CONCURRENCY (default 1)."""
    raw = os.environ.get("WEB_CONCURRENCY", "1").strip() or "1"
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


@dataclass
class DomainServices:
    """Running desk workers for one process lifespan."""

    signal_worker: SignalEngineWorker = field(default_factory=SignalEngineWorker)
    options_lab_worker: OptionsLabWorker = field(default_factory=OptionsLabWorker)
    options_lab_bots_worker: OptionsLabBotsWorker = field(
        default_factory=OptionsLabBotsWorker
    )
    kite_hub: KiteTickerHub = field(default_factory=get_kite_ticker_hub)
    # Names of services actually started by the last ``start()`` (stop only these).
    _active: list[str] = field(default_factory=list, init=False, repr=False)

    def start(self, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        self._active.clear()
        if settings.environment.lower() == "test":
            return
        # Hub first so workers can subscribe on their first tick.
        if settings.kite_ticker_enabled:
            workers = web_concurrency()
            if workers > 1:
                # Kite ~3 WS connections per api_key; each process opens its own.
                logger.error(
                    "kite_ticker_refused_multi_worker",
                    web_concurrency=workers,
                    hint="set WEB_CONCURRENCY=1 or disable KITE_TICKER_ENABLED",
                )
            else:
                self.kite_hub.start()
                self._active.append("kite")
                logger.info("domain_service_started", service="kite_ticker_hub")
        if settings.signal_engine_ticker_enabled:
            self.signal_worker.start()
            self._active.append("signal")
            logger.info("domain_service_started", service="signal_engine_worker")
        if settings.options_lab_ticker_enabled:
            self.options_lab_worker.start()
            self._active.append("options_lab")
            logger.info("domain_service_started", service="options_lab_worker")
        if settings.options_lab_bots_enabled:
            workers = web_concurrency()
            if workers > 1:
                # Unattended place path — refuse multi-process until a leader lock exists.
                logger.error(
                    "options_lab_bots_refused_multi_worker",
                    web_concurrency=workers,
                    hint="set WEB_CONCURRENCY=1 or disable OPTIONS_LAB_BOTS_ENABLED",
                )
            else:
                self.options_lab_bots_worker.start()
                self._active.append("options_lab_bots")
                logger.info("domain_service_started", service="options_lab_bots_worker")

    async def stop(self) -> None:
        # Reverse of start; only tear down what this instance actually started so
        # test-env no-op starts do not disable the shared Kite hub singleton.
        if "options_lab_bots" in self._active:
            await self.options_lab_bots_worker.stop()
        if "options_lab" in self._active:
            await self.options_lab_worker.stop()
        if "signal" in self._active:
            await self.signal_worker.stop()
        if "kite" in self._active:
            await self.kite_hub.stop()
        self._active.clear()


_services: DomainServices | None = None


def start_domain_services(settings: Settings | None = None) -> DomainServices:
    """Start desk background services once per process lifespan."""
    global _services
    if _services is not None:
        return _services
    _services = DomainServices()
    _services.start(settings)
    return _services


async def stop_domain_services() -> None:
    """Stop desk background services started by ``start_domain_services``."""
    global _services
    if _services is None:
        return
    await _services.stop()
    _services = None
