"""Tests for horizontal-scale primitives: Redis limiter, leader lock, sandbox registry, storage."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.rate_limit import HybridRateLimiter, InMemoryRateLimiter, RateLimitMiddleware, limiter
from app.scheduler.leader import LEADER_KEY, SchedulerLeaderLock
from app.storage.documents import LocalDocumentStore, display_name_from_uri
from app.tools.registry import SafeRestClient
from app.tools.sandbox.orchestrator import (
    SandboxOrchestrator,
    SandboxRunRequest,
    resolve_proxy_handler,
)


@pytest.mark.asyncio
async def test_rate_limit_middleware_enforces_in_development(monkeypatch):
    """Quotas must not be skipped just because ENVIRONMENT=development."""
    from starlette.requests import Request
    from starlette.responses import Response

    limiter.clear()
    monkeypatch.setattr(
        "app.core.rate_limit.get_settings",
        lambda: SimpleNamespace(
            is_development=True,
            rate_limits_enabled=True,
            rate_limit_per_minute=2,
            tenant_concurrency_limit=10,
        ),
    )

    async def call_next(_request: Request) -> Response:
        return Response(content=b"ok", status_code=200)

    middleware = RateLimitMiddleware(app=None)

    async def run_once() -> Response:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/admin/agents",
            "raw_path": b"/admin/agents",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("test", 80),
        }
        request = Request(scope)
        request.state.tenant = SimpleNamespace(
            tenant_id="11111111-1111-1111-1111-111111111111",
            user_id="user_dev",
        )
        return await middleware.dispatch(request, call_next)

    assert (await run_once()).status_code == 200
    assert (await run_once()).status_code == 200
    with pytest.raises(HTTPException) as exc:
        await run_once()
    assert exc.value.status_code == 429
    limiter.clear()


@pytest.mark.asyncio
async def test_memory_rate_limiter_blocks_after_limit():
    limiter = InMemoryRateLimiter()
    limiter.hit("k", limit=2)
    limiter.hit("k", limit=2)
    with pytest.raises(HTTPException) as exc:
        limiter.hit("k", limit=2)
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_hybrid_async_hit_uses_memory_without_redis(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "memory://")
    from app.core import redis_client, settings

    settings.get_settings.cache_clear()
    redis_client.reset_redis_state_for_tests()
    limiter = HybridRateLimiter()
    await limiter.async_hit("user:a", limit=1)
    with pytest.raises(HTTPException):
        await limiter.async_hit("user:a", limit=1)
    settings.get_settings.cache_clear()


@pytest.mark.asyncio
async def test_hybrid_async_hit_redis_pipeline(monkeypatch):
    redis = AsyncMock()
    redis.eval = AsyncMock(return_value=1)

    monkeypatch.setattr("app.core.rate_limit.redis_enabled", lambda: True)
    monkeypatch.setattr("app.core.rate_limit.get_redis", AsyncMock(return_value=redis))

    limiter = HybridRateLimiter()
    await limiter.async_hit("user:b", limit=5)
    redis.eval.assert_awaited()

    redis.eval = AsyncMock(return_value=0)
    with pytest.raises(HTTPException) as exc:
        await limiter.async_hit("user:b", limit=5)
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_scheduler_leader_memory_in_development(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "memory://")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from app.core import redis_client, settings

    settings.get_settings.cache_clear()
    redis_client.reset_redis_state_for_tests()
    lock = SchedulerLeaderLock()
    assert await lock.try_acquire() is True
    await lock.release()
    settings.get_settings.cache_clear()


@pytest.mark.asyncio
async def test_scheduler_leader_redis_nx(monkeypatch):
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    redis.get = AsyncMock(return_value=None)
    redis.delete = AsyncMock()
    redis.expire = AsyncMock()

    monkeypatch.setattr("app.scheduler.leader.redis_enabled", lambda: True)
    monkeypatch.setattr("app.scheduler.leader.get_redis", AsyncMock(return_value=redis))
    monkeypatch.setattr(
        "app.scheduler.leader.get_settings",
        lambda: SimpleNamespace(is_development=False),
    )

    lock = SchedulerLeaderLock()
    assert await lock.try_acquire() is True
    redis.set.assert_awaited()
    assert redis.set.await_args.args[0] == LEADER_KEY

    redis.set = AsyncMock(return_value=False)
    redis.get = AsyncMock(return_value="other")
    assert await lock.try_acquire() is False


@pytest.mark.asyncio
async def test_sandbox_redis_stores_no_authorization(monkeypatch):
    """H1 fix: Redis must never hold plaintext tenant bearer tokens."""
    import json

    stored: dict[str, str] = {}

    class FakeRedis:
        async def set(self, key: str, value: str, ex: int | None = None) -> bool:
            stored[key] = value
            return True

        async def delete(self, key: str) -> int:
            stored.pop(key, None)
            return 1

        async def get(self, key: str) -> str | None:
            return stored.get(key)

    fake = FakeRedis()
    monkeypatch.setattr(
        "app.tools.sandbox.orchestrator.get_redis", AsyncMock(return_value=fake)
    )
    monkeypatch.setattr(
        "app.tools.sandbox.orchestrator._instance_url",
        lambda: "http://owner:7777",
    )

    client = SafeRestClient({"api.example.com"})
    orch = SandboxOrchestrator(
        manager_url="",
        client=client,
        tenant_key="t1",
    )
    await orch._register_run(
        "run-1",
        SandboxRunRequest(
            source_code="pass",
            settings={},
            capability="x",
            arguments={},
            headers={"Authorization": "Bearer super-secret", "X-Trace": "1"},
        ),
    )
    raw = stored["atlas:sandbox:run:run-1"]
    assert "super-secret" not in raw
    assert "Authorization" not in raw
    assert "Bearer" not in raw
    data = json.loads(raw)
    assert data["public_headers"] == {"X-Trace": "1"}
    assert data["owner_url"] == "http://owner:7777"
    assert "headers" not in data
    # Short-lived Fernet seal is allowed so reload/other workers can proxy.
    assert isinstance(data.get("headers_enc"), str) and data["headers_enc"]


@pytest.mark.asyncio
async def test_sandbox_proxy_unseals_headers_on_other_worker(monkeypatch):
    """Sealed Redis headers let a non-owner worker complete HttpProxy."""
    import json

    from app.tools.sandbox.orchestrator import _seal_sensitive_headers

    sealed = _seal_sensitive_headers({"Authorization": "Bearer super-secret"})
    payload = {
        "allowed_hosts": ["api.example.com"],
        "public_headers": {"X-Trace": "1"},
        "headers_enc": sealed,
        "max_response_bytes": 1000,
        "timeout_seconds": 10,
        "owner_url": "http://other-owner:7777",
        "mutating": False,
    }
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=json.dumps(payload))

    monkeypatch.setattr(
        "app.tools.sandbox.orchestrator.get_redis", AsyncMock(return_value=redis)
    )
    monkeypatch.setattr(
        "app.tools.sandbox.orchestrator._instance_url",
        lambda: "http://this:7777",
    )

    orch, req, meta = await resolve_proxy_handler("missing-locally")
    assert orch is not None
    assert req is not None
    assert meta is not None
    assert req.headers.get("Authorization") == "Bearer super-secret"
    assert req.headers.get("X-Trace") == "1"


@pytest.mark.asyncio
async def test_sandbox_proxy_resolves_metadata_without_secrets(monkeypatch):
    import json

    payload = {
        "allowed_hosts": ["api.example.com"],
        "public_headers": {"X-Trace": "1"},
        "max_response_bytes": 1000,
        "timeout_seconds": 10,
        "owner_url": "http://other-owner:7777",
    }
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=json.dumps(payload))

    monkeypatch.setattr(
        "app.tools.sandbox.orchestrator.get_redis", AsyncMock(return_value=redis)
    )
    monkeypatch.setattr(
        "app.tools.sandbox.orchestrator._instance_url",
        lambda: "http://this:7777",
    )

    orch, req, meta = await resolve_proxy_handler("missing-locally")
    assert orch is not None
    assert req is not None
    assert meta is not None
    assert "Authorization" not in req.headers
    assert req.headers.get("X-Trace") == "1"
    assert meta["owner_url"] == "http://other-owner:7777"


@pytest.mark.asyncio
async def test_sandbox_http_proxy_allowlist_still_enforced():
    client = SafeRestClient({"api.example.com"})
    orch = SandboxOrchestrator(manager_url="", client=client)
    run = SandboxRunRequest(
        source_code="pass",
        settings={},
        capability="list_items",
        arguments={},
    )
    from app.tools.sandbox import orchestrator as orch_mod

    run_id = "test-run-proxy"
    orch_mod._ACTIVE[run_id] = orch
    orch_mod._RUN_REQUESTS[run_id] = run
    try:
        denied = await orch.handle_http_proxy(
            run_id, method="GET", url="https://evil.example/x"
        )
        assert denied["ok"] is False
        assert denied["status_code"] == 403
    finally:
        orch_mod._ACTIVE.pop(run_id, None)
        orch_mod._RUN_REQUESTS.pop(run_id, None)


@pytest.mark.asyncio
async def test_local_document_store_roundtrip(tmp_path: Path):
    store = LocalDocumentStore(str(tmp_path))
    uri = await store.put(tenant_id="t1", name="note.txt", data=b"hello knowledge")
    assert uri.startswith("file:")
    assert display_name_from_uri(uri).endswith("note.txt")
    assert await store.get(uri) == b"hello knowledge"
    await store.delete(uri)
    with pytest.raises(FileNotFoundError):
        await store.get(uri)


def test_display_name_from_s3_uri():
    assert display_name_from_uri("s3://bucket/tenant/abc-file.md") == "abc-file.md"


@pytest.mark.asyncio
async def test_get_redis_returns_cached_client_without_ping(monkeypatch):
    """Cached clients must not ping on every limiter/lookup call."""
    ping_mock = AsyncMock(return_value=True)

    class FakeRedis:
        async def aclose(self):
            return None

    fake = FakeRedis()
    fake.ping = ping_mock
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    from app.core import redis_client, settings

    settings.get_settings.cache_clear()
    redis_client.reset_redis_state_for_tests()
    redis_client._redis = fake

    first = await redis_client.get_redis()
    second = await redis_client.get_redis()
    assert first is fake
    assert second is fake
    ping_mock.assert_not_awaited()

    redis_client.reset_redis_state_for_tests()
    settings.get_settings.cache_clear()


@pytest.mark.asyncio
async def test_acquire_counter_slot_refreshes_ttl_every_time(monkeypatch):
    """M2: EXPIRE must run on every successful acquire, not only when count==1."""
    calls: list[tuple] = []

    class FakeRedis:
        async def eval(self, script, numkeys, *args):
            calls.append((script, numkeys, args))
            return 1

    monkeypatch.setattr(
        "app.core.redis_client.get_redis", AsyncMock(return_value=FakeRedis())
    )
    from app.core.redis_client import acquire_counter_slot

    assert await acquire_counter_slot("atlas:sandbox:conc:t", limit=4, ttl_seconds=600)
    assert len(calls) == 1
    script, _n, args = calls[0]
    assert "EXPIRE" in script
    # Script must expire even when count is not 1 (no count==1 gate).
    assert "count == 1" not in script
    assert args[0] == "atlas:sandbox:conc:t"
    assert int(args[1]) == 600
    assert int(args[2]) == 4


@pytest.mark.asyncio
async def test_acquire_zset_slot_reaps_stale_and_releases(monkeypatch):
    """Sandbox slots are named; stale holders are reaped so leaks cannot stick."""
    store: dict[str, list[tuple[float, str]]] = {}
    calls: list[str] = []

    class FakeRedis:
        async def eval(self, script, numkeys, *args):
            key = args[0]
            if "ZREMRANGEBYSCORE" in script:
                calls.append("acquire")
                now = float(args[1])
                max_age = float(args[2])
                limit = int(args[3])
                member = str(args[4])
                entries = [
                    (score, mid)
                    for score, mid in store.get(key, [])
                    if score >= now - max_age
                ]
                if len(entries) >= limit:
                    store[key] = entries
                    return 0
                entries.append((now, member))
                store[key] = entries
                return 1
            calls.append("release")
            member = str(args[1])
            store[key] = [(s, m) for s, m in store.get(key, []) if m != member]
            return 1

    monkeypatch.setattr(
        "app.core.redis_client.get_redis", AsyncMock(return_value=FakeRedis())
    )
    from app.core.redis_client import acquire_zset_slot, release_zset_slot

    key = "atlas:sandbox:slots:t"
    assert await acquire_zset_slot(
        key, member="run-a", limit=1, max_age_seconds=120, ttl_seconds=600
    )
    assert (
        await acquire_zset_slot(
            key, member="run-b", limit=1, max_age_seconds=120, ttl_seconds=600
        )
        is False
    )
    await release_zset_slot(key, "run-a")
    assert await acquire_zset_slot(
        key, member="run-b", limit=1, max_age_seconds=120, ttl_seconds=600
    )
    assert "acquire" in calls and "release" in calls
