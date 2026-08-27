"""First-party Kite REST coalescing tests."""

from __future__ import annotations

import asyncio

import pytest

from app.domains import kite_rest


@pytest.fixture(autouse=True)
def _reset() -> None:
    kite_rest.reset_kite_rest_for_tests()
    yield
    kite_rest.reset_kite_rest_for_tests()


@pytest.mark.asyncio
async def test_fetch_kite_quotes_coalesces_inflight(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    gate = asyncio.Event()

    async def fake_http(**kwargs):  # noqa: ANN003
        calls["n"] += 1
        await gate.wait()
        return {
            "NSE:NIFTY 50": {"last_price": 24500.0},
            "BSE:SENSEX": {"last_price": 80000.0},
        }

    monkeypatch.setattr(kite_rest, "_http_get_quotes", fake_http)

    t1 = asyncio.create_task(
        kite_rest.fetch_kite_quotes(
            api_key="k",
            access_token="t",
            symbols=["NSE:NIFTY 50", "BSE:SENSEX"],
            prefer="get_quote",
        )
    )
    t2 = asyncio.create_task(
        kite_rest.fetch_kite_quotes(
            api_key="k",
            access_token="t",
            symbols=["BSE:SENSEX", "NSE:NIFTY 50"],  # same set, different order
            prefer="get_quote",
        )
    )
    await asyncio.sleep(0.05)
    assert calls["n"] == 1
    gate.set()
    a, b = await asyncio.gather(t1, t2)
    assert a["NSE:NIFTY 50"]["last_price"] == 24500.0
    assert b["BSE:SENSEX"]["last_price"] == 80000.0
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_fetch_kite_quotes_empty_symbols() -> None:
    out = await kite_rest.fetch_kite_quotes(
        api_key="k",
        access_token="t",
        symbols=[],
    )
    assert out == {}


@pytest.mark.asyncio
async def test_http_get_quotes_retries_once_on_429(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    class FakeResp:
        def __init__(self, status: int, body: str = "{}", text: str = "rate"):
            self.status_code = status
            self._body = body
            self.text = text

        def json(self):
            import json

            return json.loads(self._body)

    class FakeClient:
        async def get(self, *args, **kwargs):  # noqa: ANN002,ANN003
            calls["n"] += 1
            if calls["n"] == 1:
                return FakeResp(429, text="Too Many Requests")
            return FakeResp(
                200,
                body='{"status":"success","data":{"NSE:NIFTY 50":{"last_price":1}}}',
            )

        @property
        def is_closed(self) -> bool:
            return False

    async def no_wait(*_a, **_k):
        return True

    async def fake_client(_timeout: float):
        return FakeClient()

    monkeypatch.setattr(kite_rest, "_await_rate_slot", no_wait)
    monkeypatch.setattr(kite_rest, "_get_shared_client", fake_client)

    out = await kite_rest._http_get_quotes(
        api_key="k",
        access_token="t",
        symbols=["NSE:NIFTY 50"],
        mode="quote",
        timeout_s=2.0,
    )
    assert calls["n"] == 2
    assert out["NSE:NIFTY 50"]["last_price"] == 1


@pytest.mark.asyncio
async def test_rate_slot_is_per_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Distinct api keys must not serialize behind one global slot."""
    kite_rest.reset_kite_rest_for_tests()
    # Force both keys "ready now".
    ok_a = await kite_rest._await_rate_slot("key-aaa", max_wait_s=0.0)
    ok_b = await kite_rest._await_rate_slot("key-bbb", max_wait_s=0.0)
    assert ok_a is True and ok_b is True
    # Same key immediately after reserve should refuse a zero-wait deadline.
    ok_again = await kite_rest._await_rate_slot("key-aaa", max_wait_s=0.0)
    assert ok_again is False


@pytest.mark.asyncio
async def test_history_rate_bucket_independent_of_quote() -> None:
    """History ~3/s must not share the quote 1/s slot."""
    kite_rest.reset_kite_rest_for_tests()
    assert await kite_rest._await_rate_slot("k", bucket="quote", max_wait_s=0.0)
    # Quote just reserved — history should still get an immediate slot.
    assert await kite_rest._await_rate_slot("k", bucket="history", max_wait_s=0.0)
    # Same history key refuses a second zero-wait reserve.
    assert (
        await kite_rest._await_rate_slot("k", bucket="history", max_wait_s=0.0) is False
    )


@pytest.mark.asyncio
async def test_fetch_kite_history_coalesces_and_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}
    gate = asyncio.Event()

    async def fake_http(**kwargs):  # noqa: ANN003
        calls["n"] += 1
        await gate.wait()
        return {
            "ok": True,
            "data": {
                "candles": [
                    ["2026-03-26T09:15:00+0530", 1, 2, 0.5, 1.5, 10],
                ]
            },
        }

    monkeypatch.setattr(kite_rest, "_http_get_history", fake_http)

    t1 = asyncio.create_task(
        kite_rest.fetch_kite_history(
            api_key="k",
            access_token="t",
            instrument_token=256265,
            interval="minute",
            from_date="2026-03-26 09:15:00",
            to_date="2026-03-26 15:30:00",
        )
    )
    t2 = asyncio.create_task(
        kite_rest.fetch_kite_history(
            api_key="k",
            access_token="t",
            instrument_token=256265,
            interval="minute",
            from_date="2026-03-26 09:15:00",
            to_date="2026-03-26 15:30:00",
        )
    )
    await asyncio.sleep(0.05)
    assert calls["n"] == 1
    gate.set()
    a, b = await asyncio.gather(t1, t2)
    assert a is not None and b is not None
    assert a["ok"] is True
    assert len(a["data"]["candles"]) == 1
    assert a == b
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_rate_slot_sleeps_outside_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    held_during_sleep = {"v": False}
    real_sleep = asyncio.sleep

    async def slow_sleep(dt: float):
        # If the rate lock is still held, another waiter cannot acquire it.
        contender = asyncio.create_task(kite_rest._rate_lock.acquire())
        await real_sleep(0.01)
        held_during_sleep["v"] = not contender.done()
        if contender.done():
            kite_rest._rate_lock.release()
        else:
            contender.cancel()
        await real_sleep(dt)

    monkeypatch.setattr(asyncio, "sleep", slow_sleep)
    kite_rest.reset_kite_rest_for_tests()
    kite_rest._next_allowed_at[kite_rest._rate_key("k", bucket="quote")] = (
        __import__("time").monotonic() + 0.05
    )
    assert await kite_rest._await_rate_slot("k", max_wait_s=1.0) is True
    assert held_during_sleep["v"] is False
