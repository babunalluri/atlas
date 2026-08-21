"""Domain runtime bootstrap stays out of Agent OS."""

from __future__ import annotations

import pytest

from app.domains.runtime import DomainServices, start_domain_services, stop_domain_services


@pytest.mark.asyncio
async def test_domain_services_skip_in_test_env() -> None:
    class _Settings:
        environment = "test"
        kite_ticker_enabled = True
        signal_engine_ticker_enabled = True
        options_lab_ticker_enabled = True

    started: list[str] = []

    class _Hub:
        def start(self) -> None:
            started.append("hub")

        async def stop(self) -> None:
            started.append("hub_stop")

    class _Worker:
        def start(self) -> None:
            started.append("worker")

        async def stop(self) -> None:
            started.append("worker_stop")

    services = DomainServices(
        signal_worker=_Worker(),  # type: ignore[arg-type]
        options_lab_worker=_Worker(),  # type: ignore[arg-type]
        kite_hub=_Hub(),  # type: ignore[arg-type]
    )
    services.start(_Settings())  # type: ignore[arg-type]
    assert started == []
    await services.stop()
    # Never started → stop must not tear down the shared hub / workers.
    assert started == []


@pytest.mark.asyncio
async def test_domain_services_start_order(monkeypatch) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", "1")

    class _Settings:
        environment = "development"
        kite_ticker_enabled = True
        signal_engine_ticker_enabled = True
        options_lab_ticker_enabled = True

    started: list[str] = []

    class _Hub:
        def start(self) -> None:
            started.append("hub")

        async def stop(self) -> None:
            started.append("hub_stop")

    class _Worker:
        def __init__(self, name: str) -> None:
            self.name = name

        def start(self) -> None:
            started.append(self.name)

        async def stop(self) -> None:
            started.append(f"{self.name}_stop")

    services = DomainServices(
        signal_worker=_Worker("signal"),  # type: ignore[arg-type]
        options_lab_worker=_Worker("options"),  # type: ignore[arg-type]
        kite_hub=_Hub(),  # type: ignore[arg-type]
    )
    services.start(_Settings())  # type: ignore[arg-type]
    assert started == ["hub", "signal", "options"]
    await services.stop()
    assert started == ["hub", "signal", "options", "options_stop", "signal_stop", "hub_stop"]


@pytest.mark.asyncio
async def test_domain_services_partial_flags(monkeypatch) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", "1")

    class _Settings:
        environment = "development"
        kite_ticker_enabled = True
        signal_engine_ticker_enabled = False
        options_lab_ticker_enabled = True

    started: list[str] = []

    class _Hub:
        def start(self) -> None:
            started.append("hub")

        async def stop(self) -> None:
            started.append("hub_stop")

    class _Worker:
        def __init__(self, name: str) -> None:
            self.name = name

        def start(self) -> None:
            started.append(self.name)

        async def stop(self) -> None:
            started.append(f"{self.name}_stop")

    services = DomainServices(
        signal_worker=_Worker("signal"),  # type: ignore[arg-type]
        options_lab_worker=_Worker("options"),  # type: ignore[arg-type]
        kite_hub=_Hub(),  # type: ignore[arg-type]
    )
    services.start(_Settings())  # type: ignore[arg-type]
    assert started == ["hub", "options"]
    await services.stop()
    assert "signal_stop" not in started
    assert started[-2:] == ["options_stop", "hub_stop"]


@pytest.mark.asyncio
async def test_domain_services_refuses_kite_when_multi_worker(monkeypatch) -> None:
    class _Settings:
        environment = "development"
        kite_ticker_enabled = True
        signal_engine_ticker_enabled = False
        options_lab_ticker_enabled = False

    started: list[str] = []

    class _Hub:
        def start(self) -> None:
            started.append("hub")

        async def stop(self) -> None:
            started.append("hub_stop")

    class _Worker:
        def start(self) -> None:
            started.append("worker")

        async def stop(self) -> None:
            started.append("worker_stop")

    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    services = DomainServices(
        signal_worker=_Worker(),  # type: ignore[arg-type]
        options_lab_worker=_Worker(),  # type: ignore[arg-type]
        kite_hub=_Hub(),  # type: ignore[arg-type]
    )
    services.start(_Settings())  # type: ignore[arg-type]
    assert started == []
    assert "kite" not in services._active


@pytest.mark.asyncio
async def test_start_stop_domain_services_roundtrip(monkeypatch) -> None:
    class _Settings:
        environment = "test"
        kite_ticker_enabled = False
        signal_engine_ticker_enabled = False
        options_lab_ticker_enabled = False

    monkeypatch.setattr("app.domains.runtime.get_settings", lambda: _Settings())
    await stop_domain_services()
    start_domain_services(_Settings())  # type: ignore[arg-type]
    await stop_domain_services()
    await stop_domain_services()  # idempotent


def test_agent_os_does_not_import_desk_workers() -> None:
    import pathlib

    text = pathlib.Path("src/app/agent_runtime/agent_os.py").read_text()
    assert "kite_ticker_hub" not in text
    assert "OptionsLabWorker" not in text
    assert "SignalEngineWorker" not in text
    assert "domains.runtime" in text
