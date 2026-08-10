"""Host-side orchestrator for ephemeral sandboxed Python runs."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.redis_client import get_redis
from app.core.settings import get_settings
from app.tools.registry import SafeRestClient, UnsafeOutboundRequest

logger = logging.getLogger(__name__)

MAX_RESULT_CHARS = 200_000
DEFAULT_WALL_SECONDS = 30

# Secrets (Authorization headers) stay process-local. Redis only holds non-secret
# routing metadata so another replica can forward the proxy callback to the owner.
_ACTIVE: dict[str, "SandboxOrchestrator"] = {}
_RUN_REQUESTS: dict[str, "SandboxRunRequest"] = {}
_TENANT_SLOTS: dict[str, asyncio.Semaphore] = {}
_TENANT_SLOTS_LOCK = asyncio.Lock()

_SENSITIVE_HEADER_NAMES = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "x-api-key",
        "api-key",
        "x-auth-token",
    }
)


@dataclass(slots=True)
class SandboxRunRequest:
    source_code: str
    settings: dict[str, Any]
    capability: str
    arguments: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = DEFAULT_WALL_SECONDS
    # When False, HttpProxy may only issue safe methods (GET/HEAD).
    mutating: bool = False


@dataclass(slots=True)
class SandboxRunResult:
    ok: bool
    value: Any = None
    error: str | None = None
    run_id: str = ""


def _run_redis_key(run_id: str) -> str:
    return f"atlas:sandbox:run:{run_id}"


def _conc_redis_key(tenant_key: str) -> str:
    return f"atlas:sandbox:conc:{tenant_key}"


def _instance_url() -> str:
    settings = get_settings()
    # Prefer an instance-unique URL when set (pod IP / task IP); else shared callback base.
    raw = (settings.sandbox_instance_url or settings.sandbox_callback_base_url or "").strip()
    return raw.rstrip("/")


async def _tenant_local_slot(tenant_key: str, limit: int) -> asyncio.Semaphore:
    async with _TENANT_SLOTS_LOCK:
        sem = _TENANT_SLOTS.get(tenant_key)
        if sem is None:
            sem = asyncio.Semaphore(limit)
            _TENANT_SLOTS[tenant_key] = sem
        return sem


def _public_headers(headers: dict[str, str]) -> dict[str, str]:
    """Strip credential-bearing headers — never persist these to Redis."""
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in _SENSITIVE_HEADER_NAMES
    }


class SandboxOrchestrator:
    """Talks to sandbox-manager; mediates HttpProxy via SafeRestClient."""

    def __init__(
        self,
        *,
        manager_url: str,
        client: SafeRestClient,
        callback_base_url: str = "http://backend:7777",
        concurrency_limit: int = 4,
        image: str = "atlas-sandbox-python:local",
        tenant_key: str = "default",
    ) -> None:
        self.manager_url = manager_url.rstrip("/")
        self.client = client
        self.callback_base_url = callback_base_url.rstrip("/")
        self.image = image
        self.concurrency_limit = concurrency_limit
        self.tenant_key = tenant_key

    async def run(self, request: SandboxRunRequest) -> SandboxRunResult:
        run_id = str(uuid.uuid4())
        _ACTIVE[run_id] = self
        _RUN_REQUESTS[run_id] = request
        acquired = False
        local_held = False
        local_sem: asyncio.Semaphore | None = None
        try:
            await self._register_run(run_id, request)
            try:
                acquired = await self._acquire_slot()
            except RuntimeError as exc:
                return SandboxRunResult(ok=False, error=str(exc), run_id=run_id)
            if not acquired:
                local_sem = await _tenant_local_slot(self.tenant_key, self.concurrency_limit)
                await local_sem.acquire()
                local_held = True
            return await self._invoke_manager(run_id, request)
        finally:
            if acquired:
                await self._release_slot()
            if local_held and local_sem is not None:
                local_sem.release()
            await self._unregister_run(run_id)
            _ACTIVE.pop(run_id, None)
            _RUN_REQUESTS.pop(run_id, None)

    async def _register_run(self, run_id: str, request: SandboxRunRequest) -> None:
        """Publish non-secret routing metadata only. Auth headers stay in _RUN_REQUESTS."""
        client = await get_redis()
        if client is None:
            return
        payload = {
            "allowed_hosts": sorted(self.client.allowed_hosts),
            "max_response_bytes": self.client.max_response_bytes,
            "timeout_seconds": float(
                getattr(self.client.timeout, "read", None)
                or getattr(self.client.timeout, "timeout", None)
                or 10
            ),
            "owner_url": _instance_url(),
            # Non-sensitive guest headers only (never Authorization / API keys).
            "public_headers": _public_headers(request.headers),
        }
        ttl = max(request.timeout_seconds + 60, 90)
        await client.set(_run_redis_key(run_id), json.dumps(payload), ex=ttl)

    async def _unregister_run(self, run_id: str) -> None:
        client = await get_redis()
        if client is None:
            return
        await client.delete(_run_redis_key(run_id))

    async def _acquire_slot(self) -> bool:
        """Return True when a Redis slot was acquired (caller must release)."""
        from app.core.redis_client import acquire_counter_slot

        client = await get_redis()
        if client is None:
            return False
        key = _conc_redis_key(self.tenant_key)
        ok = await acquire_counter_slot(key, limit=self.concurrency_limit, ttl_seconds=600)
        if not ok:
            raise RuntimeError("Sandbox tenant concurrency limit exceeded")
        return True

    async def _release_slot(self) -> None:
        from app.core.redis_client import release_counter_slot

        await release_counter_slot(_conc_redis_key(self.tenant_key))

    async def handle_http_proxy(
        self,
        run_id: str,
        *,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | list[Any] | None = None,
        form_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = _RUN_REQUESTS.get(run_id)
        rest_client = self.client
        if request is None:
            # Non-owner replica: never has secrets. Forward to the owning instance.
            meta = await _load_meta_from_redis(run_id)
            if meta is None:
                raise LookupError("Unknown sandbox run")
            owner = str(meta.get("owner_url") or "").rstrip("/")
            self_url = _instance_url()
            if owner and owner != self_url:
                return await _forward_proxy_to_owner(
                    owner,
                    run_id,
                    method=method,
                    url=url,
                    headers=headers,
                    json_body=json_body,
                    form_body=form_body,
                )
            raise LookupError("Sandbox run is not owned by this instance")
        merged = {**request.headers, **(headers or {})}
        allowed = (
            ("GET", "POST", "PUT", "PATCH", "DELETE")
            if request.mutating
            else ("GET", "HEAD")
        )
        try:
            response = await rest_client.request(
                method.upper(),
                url,
                headers=merged,
                json_body=json_body,
                form_body=form_body,
                allowed_methods=allowed,
            )
            raw_body = response.get("body")
            parsed: Any = raw_body
            if isinstance(raw_body, str):
                try:
                    parsed = json.loads(raw_body)
                except json.JSONDecodeError:
                    parsed = raw_body
            return {
                "ok": True,
                "status_code": response.get("status", 200),
                "body": parsed,
            }
        except UnsafeOutboundRequest as exc:
            return {"ok": False, "error": str(exc), "status_code": 403}
        except Exception as exc:  # noqa: BLE001 - surface to guest
            logger.warning("sandbox http proxy failed run_id=%s err=%s", run_id, exc)
            return {"ok": False, "error": "Upstream request failed", "status_code": 502}

    async def _invoke_manager(
        self, run_id: str, request: SandboxRunRequest
    ) -> SandboxRunResult:
        if not self.manager_url:
            return SandboxRunResult(
                ok=False,
                error="Sandbox manager is not configured (SANDBOX_MANAGER_URL)",
                run_id=run_id,
            )
        # Prefer instance-unique URL so callbacks land on the secret-owning process.
        callback_base = _instance_url() or self.callback_base_url
        payload = {
            "run_id": run_id,
            "image": self.image,
            "source_code": request.source_code,
            "settings": request.settings,
            "capability": request.capability,
            "arguments": request.arguments,
            "timeout_seconds": request.timeout_seconds,
            "proxy_base_url": f"{callback_base}/internal/sandbox/proxy/{run_id}",
        }
        try:
            async with httpx.AsyncClient(timeout=request.timeout_seconds + 15) as http:
                token = get_settings().sandbox_internal_token.get_secret_value()
                response = await http.post(
                    f"{self.manager_url}/v1/runs",
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
                body = response.json()
        except Exception as exc:  # noqa: BLE001
            return SandboxRunResult(
                ok=False, error=f"Sandbox manager error: {exc}", run_id=run_id
            )

        value = body.get("value")
        if len(str(value)) > MAX_RESULT_CHARS:
            return SandboxRunResult(
                ok=False,
                error="Sandbox result exceeded size limit",
                run_id=run_id,
            )
        return SandboxRunResult(
            ok=bool(body.get("ok")),
            value=value,
            error=body.get("error"),
            run_id=run_id,
        )


async def _load_meta_from_redis(run_id: str) -> dict[str, Any] | None:
    client = await get_redis()
    if client is None:
        return None
    raw = await client.get(_run_redis_key(run_id))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def _forward_proxy_to_owner(
    owner_url: str,
    run_id: str,
    *,
    method: str,
    url: str,
    headers: dict[str, str] | None,
    json_body: dict[str, Any] | None,
    form_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    token = get_settings().sandbox_internal_token.get_secret_value()
    target = f"{owner_url.rstrip('/')}/internal/sandbox/proxy/{run_id}"
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            response = await http.post(
                target,
                headers={"X-Sandbox-Internal-Token": token},
                json={
                    "method": method,
                    "url": url,
                    "headers": headers or {},
                    "json": json_body,
                    "form": form_body,
                },
            )
            if response.status_code >= 400:
                return {
                    "ok": False,
                    "error": f"Owner proxy HTTP {response.status_code}",
                    "status_code": response.status_code,
                }
            body = response.json()
            return body if isinstance(body, dict) else {"ok": False, "error": "Invalid owner response"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("forward sandbox proxy failed run_id=%s err=%s", run_id, exc)
        return {"ok": False, "error": "Failed to reach owning replica", "status_code": 502}


def get_orchestrator_for_run(run_id: str) -> SandboxOrchestrator | None:
    return _ACTIVE.get(run_id)


async def resolve_proxy_handler(
    run_id: str,
) -> tuple[SandboxOrchestrator | None, SandboxRunRequest | None, dict[str, Any] | None]:
    """Resolve local owner state, or Redis metadata for forward-to-owner."""
    orch = _ACTIVE.get(run_id)
    if orch is not None:
        return orch, _RUN_REQUESTS.get(run_id), None
    meta = await _load_meta_from_redis(run_id)
    if meta is None:
        return None, None, None
    hosts = set(meta.get("allowed_hosts") or [])
    rest = SafeRestClient(
        hosts,
        max_response_bytes=int(meta.get("max_response_bytes") or 1_000_000),
        timeout_seconds=float(meta.get("timeout_seconds") or 10),
    )
    # No secrets — public headers only. Credentialed proxy must forward to owner.
    request = SandboxRunRequest(
        source_code="",
        settings={},
        capability="",
        arguments={},
        headers=dict(meta.get("public_headers") or {}),
    )
    stub = SandboxOrchestrator(manager_url="", client=rest)
    return stub, request, meta
