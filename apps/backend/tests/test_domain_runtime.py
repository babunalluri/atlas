"""Domain runtime bootstrap stays out of Agent OS."""

from __future__ import annotations

import pytest

from app.domains.runtime import DomainServices, start_domain_services, stop_domain_services


def _stub_settings(
    *,
    environment: str = "development",
    kite: bool = False,
    signal: bool = False,
    options_lab: bool = False,
    bots: bool = False,
):
    class _Settings:
        pass

    s = _Settings()
    s.environment = environment
    s.kite_ticker_enabled = kite
    s.signal_engine_ticker_enabled = signal
    s.options_lab_ticker_enabled = options_lab
    s.options_lab_bots_enabled = bots
    return s


@pytest.mark.asyncio
async def test_domain_services_skip_in_test_env() -> None:
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
        options_lab_bots_worker=_Worker(),  # type: ignore[arg-type]
        kite_hub=_Hub(),  # type: ignore[arg-type]
    )
    services.start(
        _stub_settings(
            environment="test",
            kite=True,
            signal=True,
            options_lab=True,
            bots=True,
        )
    )
    assert started == []
    await services.stop()
    # Never started → stop must not tear down the shared hub / workers.
    assert started == []


@pytest.mark.asyncio
async def test_domain_services_start_order(monkeypatch) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", "1")

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
        options_lab_bots_worker=_Worker("bots"),  # type: ignore[arg-type]
        kite_hub=_Hub(),  # type: ignore[arg-type]
    )
    services.start(
        _stub_settings(kite=True, signal=True, options_lab=True, bots=True)
    )
    assert started == ["hub", "signal", "options", "bots"]
    await services.stop()
    assert started == [
        "hub",
        "signal",
        "options",
        "bots",
        "bots_stop",
        "options_stop",
        "signal_stop",
        "hub_stop",
    ]


@pytest.mark.asyncio
async def test_domain_services_partial_flags(monkeypatch) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", "1")

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
        options_lab_bots_worker=_Worker("bots"),  # type: ignore[arg-type]
        kite_hub=_Hub(),  # type: ignore[arg-type]
    )
    services.start(_stub_settings(kite=True, options_lab=True, bots=False))
    assert started == ["hub", "options"]
    await services.stop()
    assert "signal_stop" not in started
    assert "bots_stop" not in started
    assert started[-2:] == ["options_stop", "hub_stop"]


@pytest.mark.asyncio
async def test_domain_services_refuses_kite_when_multi_worker(monkeypatch) -> None:
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
        options_lab_bots_worker=_Worker(),  # type: ignore[arg-type]
        kite_hub=_Hub(),  # type: ignore[arg-type]
    )
    services.start(_stub_settings(kite=True))
    assert started == []
    assert "kite" not in services._active


@pytest.mark.asyncio
async def test_domain_services_refuses_bots_when_multi_worker(monkeypatch) -> None:
    started: list[str] = []

    class _Hub:
        def start(self) -> None:
            started.append("hub")

        async def stop(self) -> None:
            started.append("hub_stop")

    class _Worker:
        def __init__(self, name: str = "worker") -> None:
            self.name = name

        def start(self) -> None:
            started.append(self.name)

        async def stop(self) -> None:
            started.append(f"{self.name}_stop")

    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    services = DomainServices(
        signal_worker=_Worker("signal"),  # type: ignore[arg-type]
        options_lab_worker=_Worker("options"),  # type: ignore[arg-type]
        options_lab_bots_worker=_Worker("bots"),  # type: ignore[arg-type]
        kite_hub=_Hub(),  # type: ignore[arg-type]
    )
    services.start(_stub_settings(bots=True))
    assert "bots" not in started
    assert "options_lab_bots" not in services._active


@pytest.mark.asyncio
async def test_start_stop_domain_services_roundtrip(monkeypatch) -> None:
    settings = _stub_settings(environment="test")
    monkeypatch.setattr("app.domains.runtime.get_settings", lambda: settings)
    await stop_domain_services()
    start_domain_services(settings)  # type: ignore[arg-type]
    await stop_domain_services()
    await stop_domain_services()  # idempotent


def test_agent_os_does_not_import_desk_workers() -> None:
    import pathlib

    text = pathlib.Path("src/app/agent_runtime/agent_os.py").read_text()
    assert "kite_ticker_hub" not in text
    assert "OptionsLabWorker" not in text
    assert "SignalEngineWorker" not in text
    assert "OptionsLabBotsWorker" not in text
    assert "domains.runtime" in text
