"""Internal routes used by sandbox-manager HttpProxy callbacks."""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.settings import get_settings
from app.tools.sandbox.orchestrator import (
    _forward_proxy_to_owner,
    _instance_url,
    resolve_proxy_handler,
)

router = APIRouter(prefix="/internal/sandbox", tags=["internal-sandbox"])


class ProxyBody(BaseModel):
    method: str = Field(min_length=1, max_length=10)
    url: str = Field(min_length=1, max_length=2048)
    headers: dict[str, str] = Field(default_factory=dict)
    json_body: Any | None = Field(default=None, alias="json")
    form_body: dict[str, Any] | None = Field(default=None, alias="form")

    model_config = {"populate_by_name": True}


def _require_internal_token(token: str | None) -> None:
    expected = get_settings().sandbox_internal_token.get_secret_value()
    if not expected or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Unauthorized sandbox callback")


@router.post("/proxy/{run_id}")
async def sandbox_http_proxy(
    run_id: str,
    body: ProxyBody,
    x_sandbox_internal_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Mediate guest HttpProxy calls.

    Auth may arrive either as:
    - process-local ``_RUN_REQUESTS`` headers (same worker),
    - short-lived sealed ``headers_enc`` in Redis (other worker / reload), or
    - headers on this callback body (guest toolkit usually sends Authorization).
    """
    _require_internal_token(x_sandbox_internal_token)
    orchestrator, request, meta = await resolve_proxy_handler(run_id)
    if orchestrator is None:
        raise HTTPException(status_code=404, detail="Unknown sandbox run")

    from app.tools.sandbox import orchestrator as orch_mod

    local = run_id in orch_mod._RUN_REQUESTS
    guest_has_auth = any(
        k.lower()
        in {
            "authorization",
            "proxy-authorization",
            "x-api-key",
            "api-key",
            "x-auth-token",
        }
        for k in (body.headers or {})
    )
    sealed_has_auth = bool(
        request
        and any(
            k.lower()
            in {
                "authorization",
                "proxy-authorization",
                "x-api-key",
                "api-key",
                "x-auth-token",
            }
            for k in (request.headers or {})
        )
    )

    if local or sealed_has_auth or guest_has_auth or request is not None:
        try:
            return await orchestrator.handle_http_proxy(
                run_id,
                method=body.method,
                url=body.url,
                headers=body.headers,
                json_body=body.json_body,
                form_body=body.form_body,
                bound_request=None if local else request,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # No local state / sealed / guest auth — forward to owning replica if distinct.
    owner = str((meta or {}).get("owner_url") or "").rstrip("/")
    if owner and owner != _instance_url():
        return await _forward_proxy_to_owner(
            owner,
            run_id,
            method=body.method,
            url=body.url,
            headers=body.headers,
            json_body=body.json_body,
            form_body=body.form_body,
        )
    raise HTTPException(status_code=404, detail="Sandbox run is not owned by this instance")
