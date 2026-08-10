"""JWT-authenticated AG-UI and A2A interfaces backed by Atlas factories.

Native AgentOS mcp_server / scheduler and control-plane routes stay disabled.
These Atlas-owned mounts resolve published agents/teams per tenant JWT and
dispatch through AgentFactoryService / TeamFactoryService.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.factory import (
    AgentFactoryService,
    RuntimeRequest,
    TeamFactoryService,
    TeamRuntimeRequest,
)
from app.agent_runtime.persistence import runtime_session_id, runtime_user_id
from app.auth.dependencies import require_tenant
from app.db.repositories import AgentRepository, TeamRepository
from app.db.session import tenant_session
from app.tenancy.context import TenantContext

router = APIRouter(tags=["interfaces"])


async def _resolve_entity(
    *,
    kind: Literal["agent", "team"],
    slug: str,
    session: AsyncSession,
    context: TenantContext,
    session_id: str,
) -> Any:
    if kind == "agent":
        repo = AgentRepository(session, context)
        config = await repo.get_config_by_slug(slug)
        if config is None or config.published_version_id is None:
            raise HTTPException(status_code=404, detail="Published agent not found")
        try:
            return await AgentFactoryService(session, context).create(
                RuntimeRequest(
                    version_id=config.published_version_id,
                    session_id=session_id,
                    preview=False,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    repo = TeamRepository(session, context)
    config = await repo.get_config_by_slug(slug)
    if config is None or config.published_version_id is None:
        raise HTTPException(status_code=404, detail="Published team not found")
    try:
        return await TeamFactoryService(AgentFactoryService(session, context)).create(
            TeamRuntimeRequest(
                version_id=config.published_version_id,
                session_id=session_id,
                preview=False,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/interfaces/agui/status")
async def agui_status(
    context: Annotated[TenantContext, Depends(require_tenant)],
) -> dict[str, str]:
    del context
    return {"status": "available", "protocol": "ag-ui"}


@router.post("/interfaces/agui/{kind}/{slug}")
async def agui_run(
    kind: Literal["agent", "team"],
    slug: str,
    request: Request,
    context: Annotated[TenantContext, Depends(require_tenant)],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> StreamingResponse:
    try:
        from ag_ui.core import RunAgentInput
        from ag_ui.encoder import EventEncoder
        from agno.os.interfaces.agui.router import run_entity
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="AG-UI dependencies unavailable (install ag-ui-protocol)",
        ) from exc

    body = await request.json()
    try:
        run_input = RunAgentInput.model_validate(body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid AG-UI input: {exc}") from exc

    thread_id = run_input.thread_id or str(uuid.uuid4())
    entity = await _resolve_entity(
        kind=kind,
        slug=slug,
        session=session,
        context=context,
        session_id=thread_id,
    )
    await session.commit()
    encoder = EventEncoder()
    user_id = runtime_user_id(context)

    async def event_generator():
        async for event in run_entity(entity, run_input, user_id=user_id):
            yield encoder.encode(event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Session-Id": thread_id,
        },
    )


@router.get("/interfaces/a2a/status")
async def a2a_status(
    context: Annotated[TenantContext, Depends(require_tenant)],
) -> dict[str, str]:
    del context
    return {"status": "available", "protocol": "a2a"}


@router.get("/interfaces/a2a/{kind}/{slug}/.well-known/agent-card.json")
async def a2a_agent_card(
    kind: Literal["agent", "team"],
    slug: str,
    request: Request,
    context: Annotated[TenantContext, Depends(require_tenant)],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> JSONResponse:
    if kind == "agent":
        config = await AgentRepository(session, context).get_config_by_slug(slug)
    else:
        config = await TeamRepository(session, context).get_config_by_slug(slug)
    if config is None or config.published_version_id is None:
        raise HTTPException(status_code=404, detail=f"Published {kind} not found")
    base = str(request.base_url).rstrip("/")
    card = {
        "name": config.name,
        "description": config.description or f"Atlas published {kind}",
        "url": f"{base}/interfaces/a2a/{kind}/{slug}",
        "version": "1.0.0",
        "protocolVersion": "0.3",
        "capabilities": {"streaming": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {
                "id": "chat",
                "name": "Chat",
                "description": f"Run the published {kind}",
                "tags": ["atlas", kind],
            }
        ],
    }
    try:
        from a2a.types import AgentCard

        return JSONResponse(AgentCard.model_validate(card).model_dump(mode="json"))
    except Exception:
        return JSONResponse(card)


@router.post("/interfaces/a2a/{kind}/{slug}")
async def a2a_message(
    kind: Literal["agent", "team"],
    slug: str,
    request: Request,
    context: Annotated[TenantContext, Depends(require_tenant)],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> JSONResponse:
    body = await request.json()
    message_text = ""
    session_id = str(uuid.uuid4())
    if isinstance(body, dict):
        session_id = str(body.get("sessionId") or body.get("session_id") or session_id)
        # JSON-RPC style
        params = body.get("params") if isinstance(body.get("params"), dict) else body
        message = params.get("message") if isinstance(params, dict) else None
        if isinstance(message, dict):
            parts = message.get("parts") or []
            for part in parts:
                if isinstance(part, dict) and part.get("kind") == "text":
                    message_text = str(part.get("text") or "")
                    break
                if isinstance(part, dict) and "text" in part:
                    message_text = str(part.get("text") or "")
                    break
            if not message_text:
                message_text = str(message.get("text") or "")
        elif isinstance(params, dict):
            message_text = str(params.get("message") or params.get("text") or "")
    if not message_text.strip():
        raise HTTPException(status_code=400, detail="Message text is required")

    entity = await _resolve_entity(
        kind=kind,
        slug=slug,
        session=session,
        context=context,
        session_id=session_id,
    )
    await session.commit()
    durable_user_id = runtime_user_id(context)
    durable_session_id = runtime_session_id(context, session_id)

    from app.api.public_email import _collect_run_text

    try:
        text, paused = await _collect_run_text(
            entity,
            message_text,
            user_id=durable_user_id,
            session_id=durable_session_id,
            context=context,
            trace_id=None,
            external_session_id=session_id,
            wall_seconds=120,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if paused:
        text = text or "Run paused for approval."

    result = {
        "jsonrpc": "2.0",
        "id": body.get("id") if isinstance(body, dict) else None,
        "result": {
            "kind": "message",
            "role": "agent",
            "parts": [{"kind": "text", "text": text}],
            "messageId": str(uuid.uuid4()),
            "contextId": session_id,
        },
    }
    return JSONResponse(result)
