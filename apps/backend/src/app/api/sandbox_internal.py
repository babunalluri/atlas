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
    _require_internal_token(x_sandbox_internal_token)
    orchestrator, request, meta = await resolve_proxy_handler(run_id)
    if orchestrator is None:
        raise HTTPException(status_code=404, detail="Unknown sandbox run")

    # Local owner has secrets in _RUN_REQUESTS.
    from app.tools.sandbox import orchestrator as orch_mod

    if run_id in orch_mod._RUN_REQUESTS:
        try:
            return await orchestrator.handle_http_proxy(
                run_id,
                method=body.method,
                url=body.url,
                headers=body.headers,
                json_body=body.json_body,
                form_body=body.form_body,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Non-owner: forward to the instance that holds credentials (never invent secrets).
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
    if request is None:
        raise HTTPException(status_code=404, detail="Unknown sandbox run")
    # Same-instance metadata without local secrets (process restarted mid-run).
    raise HTTPException(status_code=404, detail="Sandbox run is not owned by this instance")
