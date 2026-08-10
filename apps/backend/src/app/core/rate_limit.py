"""Rate and concurrency limits — Redis-backed when configured, else in-memory."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from threading import Lock

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response, StreamingResponse

from app.core.redis_client import (
    acquire_counter_slot,
    get_redis,
    invalidate_redis,
    redis_enabled,
    release_counter_slot,
)
from app.core.settings import get_settings

_MIDDLEWARE_SKIP_PREFIXES = (
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/public/",
    "/internal/sandbox/",
    "/favicon.ico",
)


class InMemoryRateLimiter:
    """Process-local limiter used for tests and when Redis is disabled."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._inflight: dict[str, int] = defaultdict(int)
        self._lock = Lock()

    def hit(self, key: str, *, limit: int, window_seconds: int = 60) -> None:
        now = time.monotonic()
        with self._lock:
            bucket = self._events[key]
            while bucket and now - bucket[0] > window_seconds:
                bucket.popleft()
            if len(bucket) >= limit:
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
            bucket.append(now)

    def acquire(self, key: str, *, limit: int) -> None:
        with self._lock:
            if self._inflight[key] >= limit:
                raise HTTPException(status_code=429, detail="Tenant concurrency limit exceeded")
            self._inflight[key] += 1

    def release(self, key: str) -> None:
        with self._lock:
            self._inflight[key] = max(0, self._inflight[key] - 1)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._inflight.clear()


class HybridRateLimiter:
    """Async facade: Redis when REDIS_URL is set, otherwise in-memory."""

    def __init__(self) -> None:
        self._memory = InMemoryRateLimiter()

    def hit(self, key: str, *, limit: int, window_seconds: int = 60) -> None:
        self._memory.hit(key, limit=limit, window_seconds=window_seconds)

    def acquire(self, key: str, *, limit: int) -> None:
        self._memory.acquire(key, limit=limit)

    def release(self, key: str) -> None:
        self._memory.release(key)

    def clear(self) -> None:
        self._memory.clear()

    @property
    def _events(self) -> dict[str, deque[float]]:
        return self._memory._events

    @property
    def _inflight(self) -> dict[str, int]:
        return self._memory._inflight

    async def async_hit(self, key: str, *, limit: int, window_seconds: int = 60) -> None:
        if not redis_enabled():
            self._memory.hit(key, limit=limit, window_seconds=window_seconds)
            return
        client = await get_redis()
        if client is None:
            self._memory.hit(key, limit=limit, window_seconds=window_seconds)
            return
        now = time.time()
        member = f"{now}:{uuid.uuid4().hex}"
        redis_key = f"atlas:rl:{key}"
        script = """
        redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, ARGV[1])
        local count = redis.call('ZCARD', KEYS[1])
        if count >= tonumber(ARGV[2]) then
          return 0
        end
        redis.call('ZADD', KEYS[1], ARGV[3], ARGV[4])
        redis.call('EXPIRE', KEYS[1], ARGV[5])
        return 1
        """
        try:
            allowed = await client.eval(
                script,
                1,
                redis_key,
                now - window_seconds,
                limit,
                now,
                member,
                window_seconds + 1,
            )
        except Exception:
            await invalidate_redis()
            self._memory.hit(key, limit=limit, window_seconds=window_seconds)
            return
        if int(allowed) != 1:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")

    async def async_acquire(self, key: str, *, limit: int) -> None:
        if not redis_enabled():
            self._memory.acquire(key, limit=limit)
            return
        client = await get_redis()
        if client is None:
            self._memory.acquire(key, limit=limit)
            return
        try:
            ok = await acquire_counter_slot(f"atlas:conc:{key}", limit=limit, ttl_seconds=600)
        except Exception:
            await invalidate_redis()
            self._memory.acquire(key, limit=limit)
            return
        if not ok:
            raise HTTPException(status_code=429, detail="Tenant concurrency limit exceeded")

    async def async_release(self, key: str) -> None:
        if not redis_enabled():
            self._memory.release(key)
            return
        client = await get_redis()
        if client is None:
            self._memory.release(key)
            return
        try:
            await release_counter_slot(f"atlas:conc:{key}")
        except Exception:
            await invalidate_redis()
            self._memory.release(key)


limiter = HybridRateLimiter()


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if path in {"/health", "/docs", "/openapi.json", "/redoc", "/favicon.ico"}:
            return await call_next(request)
        if path.startswith(_MIDDLEWARE_SKIP_PREFIXES):
            return await call_next(request)

        settings = get_settings()
        # Explicit opt-out only (e.g. local load tests). Do not gate on
        # ENVIRONMENT=development — that silently disabled quotas in compose.
        if not settings.rate_limits_enabled:
            return await call_next(request)

        tenant = getattr(request.state, "tenant", None)
        if tenant is None:
            # Unauthenticated / auth-skipped routes must not share one "unknown" bucket.
            return await call_next(request)

        host = request.client.host if request.client else "anon"
        user_key = getattr(tenant, "user_id", None) or host
        tenant_key = str(tenant.tenant_id)

        await limiter.async_hit(f"user:{user_key}", limit=settings.rate_limit_per_minute)
        await limiter.async_hit(
            f"tenant:{tenant_key}", limit=settings.rate_limit_per_minute * 5
        )
        conc_key = f"concurrency:{tenant_key}"
        await limiter.async_acquire(conc_key, limit=settings.tenant_concurrency_limit)
        try:
            response = await call_next(request)
        except Exception:
            await limiter.async_release(conc_key)
            raise

        if isinstance(response, StreamingResponse):
            original = response.body_iterator

            async def guarded() -> AsyncIterator[bytes]:
                try:
                    async for chunk in original:
                        yield chunk
                finally:
                    await limiter.async_release(conc_key)

            response.body_iterator = guarded()
            return response

        await limiter.async_release(conc_key)
        return response
