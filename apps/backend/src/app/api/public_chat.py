"""Anonymous / guest customer chat runs for published agents, teams, and workflows.

Security model:
- Resolved only by tenant slug + published resource slug (never by draft).
- No Clerk org membership required; guest identity is a client-supplied opaque id.
- Rate-limited per guest and per IP; never exposes admin APIs or secrets.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Form, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.agent_runtime.factory import (
    AgentFactoryService,
    RuntimeRequest,
    TeamFactoryService,
    TeamRuntimeRequest,
    WorkflowFactoryService,
    WorkflowRuntimeRequest,
)
from app.agent_runtime.persistence import runtime_session_id, runtime_user_id
from app.core.logging import get_logger
from app.core.rate_limit import limiter
from app.core.settings import get_settings
from app.db.models import Role, Tenant
from app.db.repositories import (
    AgentRepository,
    SessionRepository,
    TeamRepository,
    TenantAdminRepository,
    WorkflowRepository,
)
from app.db.session import SessionFactory
from app.tenancy.context import TenantContext, set_tenant_context

logger = get_logger(__name__)

router = APIRouter(prefix="/public", tags=["public-chat"])

_GUEST_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{8,64}$")


def _guest_user_id(raw: str | None, *, fallback_host: str) -> str:
    candidate = (raw or "").strip()
    if candidate and _GUEST_ID_RE.match(candidate):
        return f"guest:{candidate}"
    host = re.sub(r"[^a-zA-Z0-9_.-]", "_", fallback_host)[:48] or "anon"
    return f"guest:ip:{host}"


def _rate_limit_public_chat(
    *,
    tenant_id: uuid.UUID,
    guest_user_id: str,
    client_host: str,
) -> None:
    settings = get_settings()
    per_guest = max(1, settings.public_chat_rate_limit_per_minute)
    per_ip = max(per_guest, settings.public_chat_rate_limit_per_minute * 3)
    limiter.hit(f"public-chat:guest:{tenant_id}:{guest_user_id}", limit=per_guest)
    limiter.hit(f"public-chat:ip:{client_host}", limit=per_ip)
    limiter.acquire(
        f"public-chat:concurrency:{tenant_id}",
        limit=settings.tenant_concurrency_limit,
    )


@asynccontextmanager
async def _public_run_context(
    tenant_slug: str,
    *,
    guest_user_id: str,
) -> AsyncIterator[tuple[Any, Tenant, TenantContext]]:
    async with SessionFactory() as session:
        tenant = await TenantAdminRepository(session).get_by_slug(tenant_slug)
        if tenant is None or not tenant.is_active:
            raise HTTPException(status_code=404, detail="Tenant not found")
        if session.bind and session.bind.dialect.name == "postgresql":
            from sqlalchemy import text

            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant.id)},
            )
        session.info["tenant_id"] = tenant.id
        context = TenantContext(
            tenant_id=tenant.id,
            user_id=guest_user_id,
            role=Role.end_user,
            clerk_org_id=tenant.clerk_org_id,
            principal_type="guest",
        )
        set_tenant_context(context)
        yield session, tenant, context


def _streaming_response(
    event_stream: AsyncIterator[bytes],
    *,
    session_id: str,
    tenant_id: uuid.UUID,
) -> StreamingResponse:
    async def guarded() -> AsyncIterator[bytes]:
        try:
            async for item in event_stream:
                yield item
        finally:
            limiter.release(f"public-chat:concurrency:{tenant_id}")

    return StreamingResponse(
        guarded(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Session-Id": session_id,
        },
    )


@router.post("/t/{tenant_slug}/agents/{agent_slug}/runs")
async def run_public_agent(
    request: Request,
    tenant_slug: str,
    agent_slug: str,
    message: str = Form(...),
    session_id: str = Form(...),
    stream: bool = Form(True),
    x_guest_id: str | None = Header(default=None),
) -> StreamingResponse:
    """Stream a published agent run for anonymous / guest customers."""
    del stream
    from app.agent_runtime.agent_os import (
        _fail_runtime_trace,
        _persist_runtime_event,
        _sse_from_agent,
        _start_runtime_trace,
    )

    message = message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message must not be empty")
    host = request.client.host if request.client else "anon"
    guest_user_id = _guest_user_id(x_guest_id, fallback_host=host)
    acquired = False

    async with _public_run_context(tenant_slug, guest_user_id=guest_user_id) as (
        session,
        tenant,
        context,
    ):
        try:
            _rate_limit_public_chat(
                tenant_id=tenant.id,
                guest_user_id=guest_user_id,
                client_host=host,
            )
            acquired = True
            repo = AgentRepository(session, context)
            session_repo = SessionRepository(session, context)
            try:
                config = await repo.get_config_by_slug(agent_slug)
                if config is None or config.published_version_id is None:
                    raise LookupError("Published agent not found")
                existing = await session_repo.get_by_external(session_id)
                if existing is not None:
                    if existing.user_id != context.user_id:
                        raise PermissionError("Session belongs to another user")
                    if existing.target_type != "agent":
                        raise ValueError("Session is pinned to another target")
                    if existing.agent_config_id != config.id:
                        raise ValueError("Session is pinned to another agent")
                    resolved_version_id = existing.agent_version_id
                    if resolved_version_id is None:
                        raise ValueError("Session is missing a pinned agent version")
                else:
                    resolved_version_id = config.published_version_id
                agent = await AgentFactoryService(session, context).create(
                    RuntimeRequest(
                        version_id=resolved_version_id,
                        session_id=session_id,
                        preview=False,
                    )
                )
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            await session.commit()

            durable_user_id = runtime_user_id(context)
            durable_session_id = runtime_session_id(context, session_id)
            metadata = dict(getattr(agent, "_saas_metadata", {}) or {})
            metadata["public_chat"] = True
            trace_id = await _start_runtime_trace(
                context=context,
                external_session_id=session_id,
                target_id=uuid.UUID(str(metadata["agent_id"])),
                version_id=resolved_version_id,
                name="Public agent chat",
                message=message,
                metadata=metadata,
            )

            async def event_stream() -> AsyncIterator[bytes]:
                try:
                    async for item in _sse_from_agent(
                        agent,
                        message,
                        user_id=durable_user_id,
                        session_id=durable_session_id,
                        event_handler=lambda payload: _persist_runtime_event(
                            payload,
                            context=context,
                            trace_id=trace_id,
                            external_session_id=session_id,
                            initial_title=message[:255],
                        ),
                    ):
                        yield item
                except Exception as exc:
                    logger.exception("public_agent_run_failed", error=str(exc))
                    await _fail_runtime_trace(
                        context, trace_id, "Public agent run failed"
                    )
                    yield b'data: {"event":"RunError","error":"Agent run failed"}\n\n'

            return _streaming_response(
                event_stream(), session_id=session_id, tenant_id=tenant.id
            )
        except Exception:
            if acquired:
                limiter.release(f"public-chat:concurrency:{tenant.id}")
            raise


@router.post("/t/{tenant_slug}/teams/{team_slug}/runs")
async def run_public_team(
    request: Request,
    tenant_slug: str,
    team_slug: str,
    message: str = Form(...),
    session_id: str = Form(...),
    stream: bool = Form(True),
    x_guest_id: str | None = Header(default=None),
) -> StreamingResponse:
    """Stream a published team run for anonymous / guest customers."""
    del stream
    from app.agent_runtime.agent_os import (
        _fail_runtime_trace,
        _persist_runtime_event,
        _sse_from_agent,
        _start_runtime_trace,
    )

    message = message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message must not be empty")
    host = request.client.host if request.client else "anon"
    guest_user_id = _guest_user_id(x_guest_id, fallback_host=host)
    acquired = False

    async with _public_run_context(tenant_slug, guest_user_id=guest_user_id) as (
        session,
        tenant,
        context,
    ):
        try:
            _rate_limit_public_chat(
                tenant_id=tenant.id,
                guest_user_id=guest_user_id,
                client_host=host,
            )
            acquired = True
            repo = TeamRepository(session, context)
            session_repo = SessionRepository(session, context)
            try:
                config = await repo.get_config_by_slug(team_slug)
                if config is None or config.published_version_id is None:
                    raise LookupError("Published team not found")
                existing = await session_repo.get_by_external(session_id)
                if existing is not None:
                    if existing.user_id != context.user_id:
                        raise PermissionError("Session belongs to another user")
                    if existing.target_type != "team":
                        raise ValueError("Session is pinned to another target")
                    if existing.team_config_id != config.id:
                        raise ValueError("Session is pinned to another team")
                    resolved_version_id = existing.team_version_id
                    if resolved_version_id is None:
                        raise ValueError("Session is missing a pinned team version")
                else:
                    resolved_version_id = config.published_version_id
                team = await TeamFactoryService(
                    AgentFactoryService(session, context)
                ).create(
                    TeamRuntimeRequest(
                        version_id=resolved_version_id,
                        session_id=session_id,
                        preview=False,
                    )
                )
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            await session.commit()

            durable_user_id = runtime_user_id(context)
            durable_session_id = runtime_session_id(context, session_id)
            metadata = dict(getattr(team, "_saas_metadata", {}) or {})
            metadata["public_chat"] = True
            trace_id = await _start_runtime_trace(
                context=context,
                external_session_id=session_id,
                target_id=uuid.UUID(str(metadata["team_id"])),
                version_id=resolved_version_id,
                name="Public team chat",
                message=message,
                metadata=metadata,
            )

            async def event_stream() -> AsyncIterator[bytes]:
                try:
                    async for item in _sse_from_agent(
                        team,
                        message,
                        user_id=durable_user_id,
                        session_id=durable_session_id,
                        event_handler=lambda payload: _persist_runtime_event(
                            payload,
                            context=context,
                            trace_id=trace_id,
                            external_session_id=session_id,
                            initial_title=message[:255],
                        ),
                    ):
                        yield item
                except Exception as exc:
                    logger.exception("public_team_run_failed", error=str(exc))
                    await _fail_runtime_trace(
                        context, trace_id, "Public team run failed"
                    )
                    yield b'data: {"event":"RunError","error":"Team run failed"}\n\n'

            return _streaming_response(
                event_stream(), session_id=session_id, tenant_id=tenant.id
            )
        except Exception:
            if acquired:
                limiter.release(f"public-chat:concurrency:{tenant.id}")
            raise


@router.post("/t/{tenant_slug}/workflows/{workflow_slug}/runs")
async def run_public_workflow(
    request: Request,
    tenant_slug: str,
    workflow_slug: str,
    message: str = Form(...),
    session_id: str = Form(...),
    stream: bool = Form(True),
    x_guest_id: str | None = Header(default=None),
) -> StreamingResponse:
    """Stream a published workflow run for anonymous / guest customers."""
    del stream
    from app.agent_runtime.agent_os import (
        _fail_runtime_trace,
        _persist_runtime_event,
        _sse_from_agent,
        _start_runtime_trace,
    )

    message = message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message must not be empty")
    host = request.client.host if request.client else "anon"
    guest_user_id = _guest_user_id(x_guest_id, fallback_host=host)
    acquired = False

    async with _public_run_context(tenant_slug, guest_user_id=guest_user_id) as (
        session,
        tenant,
        context,
    ):
        try:
            _rate_limit_public_chat(
                tenant_id=tenant.id,
                guest_user_id=guest_user_id,
                client_host=host,
            )
            acquired = True
            repo = WorkflowRepository(session, context)
            session_repo = SessionRepository(session, context)
            try:
                config = await repo.get_config_by_slug(workflow_slug)
                if config is None or config.published_version_id is None:
                    raise LookupError("Published workflow not found")
                existing = await session_repo.get_by_external(session_id)
                if existing is not None:
                    if existing.user_id != context.user_id:
                        raise PermissionError("Session belongs to another user")
                    if existing.target_type != "workflow":
                        raise ValueError("Session is pinned to another target")
                    if existing.workflow_config_id != config.id:
                        raise ValueError("Session is pinned to another workflow")
                    resolved_version_id = existing.workflow_version_id
                    if resolved_version_id is None:
                        raise ValueError("Session is missing a pinned workflow version")
                else:
                    resolved_version_id = config.published_version_id
                workflow = await WorkflowFactoryService(
                    AgentFactoryService(session, context)
                ).create(
                    WorkflowRuntimeRequest(
                        version_id=resolved_version_id,
                        session_id=session_id,
                        preview=False,
                    )
                )
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            await session.commit()

            durable_user_id = runtime_user_id(context)
            durable_session_id = runtime_session_id(context, session_id)
            metadata = dict(getattr(workflow, "_saas_metadata", {}) or {})
            metadata["public_chat"] = True
            trace_id = await _start_runtime_trace(
                context=context,
                external_session_id=session_id,
                target_id=uuid.UUID(str(metadata["workflow_id"])),
                version_id=resolved_version_id,
                name="Public workflow chat",
                message=message,
                metadata=metadata,
            )

            async def event_stream() -> AsyncIterator[bytes]:
                try:
                    async for item in _sse_from_agent(
                        workflow,
                        message,
                        user_id=durable_user_id,
                        session_id=durable_session_id,
                        event_handler=lambda payload: _persist_runtime_event(
                            payload,
                            context=context,
                            trace_id=trace_id,
                            external_session_id=session_id,
                            initial_title=message[:255],
                        ),
                    ):
                        yield item
                except Exception as exc:
                    logger.exception("public_workflow_run_failed", error=str(exc))
                    await _fail_runtime_trace(
                        context, trace_id, "Public workflow run failed"
                    )
                    yield b'data: {"event":"RunError","error":"Workflow run failed"}\n\n'

            return _streaming_response(
                event_stream(), session_id=session_id, tenant_id=tenant.id
            )
        except Exception:
            if acquired:
                limiter.release(f"public-chat:concurrency:{tenant.id}")
            raise
