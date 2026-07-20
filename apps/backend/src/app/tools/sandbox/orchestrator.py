"""Host-side orchestrator for ephemeral sandboxed Python runs."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.tools.registry import SafeRestClient, UnsafeOutboundRequest

logger = logging.getLogger(__name__)

MAX_RESULT_CHARS = 200_000
DEFAULT_WALL_SECONDS = 30

# Process-local registry so the internal proxy route can find the orchestrator.
_ACTIVE: dict[str, "SandboxOrchestrator"] = {}
_RUN_REQUESTS: dict[str, "SandboxRunRequest"] = {}


@dataclass(slots=True)
class SandboxRunRequest:
    source_code: str
    settings: dict[str, Any]
    capability: str
    arguments: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = DEFAULT_WALL_SECONDS


@dataclass(slots=True)
class SandboxRunResult:
    ok: bool
    value: Any = None
    error: str | None = None
    run_id: str = ""


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
    ) -> None:
        self.manager_url = manager_url.rstrip("/")
        self.client = client
        self.callback_base_url = callback_base_url.rstrip("/")
        self.image = image
        self._semaphore = asyncio.Semaphore(concurrency_limit)

    async def run(self, request: SandboxRunRequest) -> SandboxRunResult:
        run_id = str(uuid.uuid4())
        _ACTIVE[run_id] = self
        _RUN_REQUESTS[run_id] = request
        try:
            async with self._semaphore:
                return await self._invoke_manager(run_id, request)
        finally:
            _ACTIVE.pop(run_id, None)
            _RUN_REQUESTS.pop(run_id, None)

    async def handle_http_proxy(
        self,
        run_id: str,
        *,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = _RUN_REQUESTS.get(run_id)
        if request is None:
            raise LookupError("Unknown sandbox run")
        merged = {**request.headers, **(headers or {})}
        try:
            response = await self.client.request(
                method.upper(),
                url,
                headers=merged,
                json_body=json_body,
                allowed_methods=("GET", "POST", "PUT", "PATCH", "DELETE"),
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
        payload = {
            "run_id": run_id,
            "image": self.image,
            "source_code": request.source_code,
            "settings": request.settings,
            "capability": request.capability,
            "arguments": request.arguments,
            "timeout_seconds": request.timeout_seconds,
            "proxy_base_url": f"{self.callback_base_url}/internal/sandbox/proxy/{run_id}",
        }
        try:
            async with httpx.AsyncClient(timeout=request.timeout_seconds + 15) as http:
                response = await http.post(f"{self.manager_url}/v1/runs", json=payload)
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


def get_orchestrator_for_run(run_id: str) -> SandboxOrchestrator | None:
    return _ACTIVE.get(run_id)
