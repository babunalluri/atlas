import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.settings import get_settings


class InMemoryRateLimiter:
    """Local development limiter. Replace with Redis/API Gateway in production."""

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


limiter = InMemoryRateLimiter()


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in {"/health", "/docs", "/openapi.json", "/redoc"}:
            return await call_next(request)

        settings = get_settings()
        # Local/dev traffic (SSR, HMR, retries) easily exceeds the default 60/min
        # when many requests share the Docker gateway IP before tenant auth runs.
        # Public chat enforces its own limiter inside the run handlers.
        if settings.is_development:
            return await call_next(request)

        tenant = getattr(request.state, "tenant", None)
        host = request.client.host if request.client else "anon"
        user_key = getattr(tenant, "user_id", None) or host
        tenant_key = str(getattr(tenant, "tenant_id", "unknown"))

        limiter.hit(f"user:{user_key}", limit=settings.rate_limit_per_minute)
        limiter.hit(f"tenant:{tenant_key}", limit=settings.rate_limit_per_minute * 5)
        limiter.acquire(f"concurrency:{tenant_key}", limit=settings.tenant_concurrency_limit)
        try:
            return await call_next(request)
        finally:
            limiter.release(f"concurrency:{tenant_key}")
