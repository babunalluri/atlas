"""Internal routes used by sandbox-manager HttpProxy callbacks."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.tools.sandbox.orchestrator import get_orchestrator_for_run

router = APIRouter(prefix="/internal/sandbox", tags=["internal-sandbox"])


class ProxyBody(BaseModel):
    method: str = Field(min_length=1, max_length=10)
    url: str = Field(min_length=1, max_length=2048)
    headers: dict[str, str] = Field(default_factory=dict)
    json: dict[str, Any] | None = None


@router.post("/proxy/{run_id}")
async def sandbox_http_proxy(run_id: str, body: ProxyBody) -> dict[str, Any]:
    orchestrator = get_orchestrator_for_run(run_id)
    if orchestrator is None:
        raise HTTPException(status_code=404, detail="Unknown sandbox run")
    try:
        return await orchestrator.handle_http_proxy(
            run_id,
            method=body.method,
            url=body.url,
            headers=body.headers,
            json_body=body.json,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
