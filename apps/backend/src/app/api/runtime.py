import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.factory import AgentFactory, RuntimeRequest
from app.auth.dependencies import require_tenant
from app.core.settings import Settings, get_settings
from app.db.session import tenant_session
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/api/runtime", tags=["runtime"])


class RunRequest(BaseModel):
    version_id: uuid.UUID
    session_id: str = Field(min_length=1, max_length=255)
    message: str = Field(min_length=1, max_length=100_000)
    preview: bool = False


def _event_payload(event: Any) -> dict[str, Any]:
    if hasattr(event, "to_dict"):
        value = event.to_dict()
        if isinstance(value, dict):
            return value
    return {
        "event": str(getattr(event, "event", "RunContent")),
        "content": str(getattr(event, "content", event)),
    }


@router.post("/runs")
async def run_agent(
    payload: RunRequest,
    context: Annotated[TenantContext, Depends(require_tenant)],
    session: Annotated[AsyncSession, Depends(tenant_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    factory = AgentFactory(session, context, settings.allowed_outbound_hosts)
    try:
        agent = await factory.create(
            RuntimeRequest(payload.version_id, payload.session_id, payload.preview)
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def stream() -> AsyncIterator[str]:
        try:
            result = agent.arun(
                payload.message,
                session_id=payload.session_id,
                user_id=context.user_id,
                stream=True,
            )
            if hasattr(result, "__await__"):
                result = await result
            if hasattr(result, "__aiter__"):
                async for event in result:
                    data = json.dumps(_event_payload(event), default=str)
                    yield f"event: message\ndata: {data}\n\n"
            else:
                data = json.dumps(_event_payload(result), default=str)
                yield f"event: message\ndata: {data}\n\n"
            yield 'event: done\ndata: {"done":true}\n\n'
        except Exception:
            # Error details stay in server traces; do not leak provider/tool secrets.
            yield 'event: error\ndata: {"error":"Agent run failed"}\n\n'

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
