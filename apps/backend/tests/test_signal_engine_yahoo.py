"""Yahoo slow-tier fetcher — cache and rate-limit safety."""

from __future__ import annotations

import time

import pytest

from app.domains import signal_engine_yahoo as yahoo


def test_fetch_serves_cache_without_refetch(monkeypatch: pytest.MonkeyPatch) -> None:
    yahoo.reset_yahoo_cache_for_tests()
    calls = {"n": 0}

    def fake_raw(tickers: dict[str, str]) -> tuple[dict[str, float], str | None]:
        calls["n"] += 1
        return {"global_dow_jones_chg": -0.42}, None

    monkeypatch.setattr(yahoo, "_fetch_raw_changes", fake_raw)
    t0 = 1000.0
    first = yahoo.fetch_yahoo_changes({"global_dow_jones_chg": "^DJI"}, now=t0, force=True)
    second = yahoo.fetch_yahoo_changes({"global_dow_jones_chg": "^DJI"}, now=t0 + 10)
    assert first == {"global_dow_jones_chg": -0.42}
    assert second == first
    assert calls["n"] == 1


def test_rate_limit_sets_cooldown_and_serves_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    yahoo.reset_yahoo_cache_for_tests()
    tickers = {"global_dow_jones_chg": "^DJI"}
    cache = yahoo._cache_for(tickers)
    cache.values = {"global_dow_jones_chg": -0.1}
    cache.fetched_at = 500.0

    class FakeRateLimit(Exception):
        pass

    def boom(_tickers: dict[str, str]) -> tuple[dict[str, float], str | None]:
        raise FakeRateLimit("Too Many Requests. Rate limited.")

    monkeypatch.setattr(yahoo, "_is_rate_limit_error", lambda exc: isinstance(exc, FakeRateLimit))
    monkeypatch.setattr(yahoo, "_fetch_raw_changes", boom)

    ts = 500.0 + yahoo.YAHOO_CACHE_TTL_SECONDS + 1
    result = yahoo.fetch_yahoo_changes(tickers, now=ts, force=True)
    assert result == {"global_dow_jones_chg": -0.1}
    assert cache.cooldown_until > ts

    # While cooling down, must not hit network again.
    calls = {"n": 0}

    def count(_tickers: dict[str, str]) -> tuple[dict[str, float], str | None]:
        calls["n"] += 1
        return {}, None

    monkeypatch.setattr(yahoo, "_fetch_raw_changes", count)
    again = yahoo.fetch_yahoo_changes({"global_dow_jones_chg": "^DJI"}, now=ts + 5)
    assert again == {"global_dow_jones_chg": -0.1}
    assert calls["n"] == 0


def test_change_from_closes() -> None:
    import pandas as pd

    closes = pd.Series([100.0, 101.5])
    assert yahoo._change_from_closes(closes) == 1.5


def test_parse_download_single_ticker() -> None:
    import pandas as pd

    frame = pd.DataFrame({"Close": [100.0, 102.0]})
    parsed = yahoo._parse_download_frame(frame, ["^DJI"])
    assert parsed["^DJI"] == 2.0


def test_fetch_cache_isolated_per_ticker_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: global cache caused CRYPTO fetch to hit ALL_ payload cache."""
    yahoo.reset_yahoo_cache_for_tests()
    seen: list[frozenset[str]] = []

    def fake_raw(tickers: dict[str, str]) -> tuple[dict[str, float], str | None]:
        seen.append(frozenset(tickers.keys()))
        return {key: float(len(key)) for key in tickers}, None

    monkeypatch.setattr(yahoo, "_fetch_raw_changes", fake_raw)
    t0 = 1000.0
    dow = {"global_dow_jones_chg": "^DJI"}
    crypto = dict(yahoo.CRYPTO_YAHOO_TICKERS)

    first = yahoo.fetch_yahoo_changes(dow, now=t0, force=True)
    second = yahoo.fetch_yahoo_changes(crypto, now=t0 + 1)
    third = yahoo.fetch_yahoo_changes(dow, now=t0 + 2)

    assert len(first) == 1
    assert len(second) == len(crypto)
    assert third == first
    assert len(seen) == 2


def test_crypto_max_abs_change_includes_bitcoin_from_global_feed() -> None:
    assert yahoo.crypto_max_abs_change(
        {"global_bitcoin_chg": -2.5, "global_eth_chg": 1.1},
    ) == 2.5


def test_session_fetch_uses_short_ttl_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    yahoo.reset_yahoo_cache_for_tests()
    calls = {"n": 0}

    def fake_raw(tickers: dict[str, str]) -> tuple[dict[str, float], str | None]:
        calls["n"] += 1
        return {"gold_chg": -0.31}, None

    monkeypatch.setattr(yahoo, "_fetch_raw_session_changes", fake_raw)
    t0 = 2000.0
    first = yahoo.fetch_yahoo_session_changes({"gold_chg": "GC=F"}, now=t0, force=True)
    second = yahoo.fetch_yahoo_session_changes({"gold_chg": "GC=F"}, now=t0 + 60)
    third = yahoo.fetch_yahoo_session_changes({"gold_chg": "GC=F"}, now=t0 + 601)
    assert first == {"gold_chg": -0.31}
    assert second == first
    assert third == first
    assert calls["n"] == 2


def test_prev_session_close_prefers_prior_day() -> None:
    import pandas as pd

    closes = pd.Series([100.0, 110.0, 108.0])
    assert yahoo._prev_session_close(closes) == 110.0
    assert yahoo._last_close(closes) == 108.0
    assert yahoo._pct_change(108.0, 110.0) == pytest.approx(-1.818, abs=0.001)
