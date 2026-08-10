"""Anonymous / guest customer chat runs for published teams and workflows.

Security model:
- Resolved only by tenant slug + published resource slug (never by draft).
- Agents are not publicly callable; they only run as members of teams/workflows.
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


async def _with_verified_identity(
    session: Any,
    context: TenantContext,
    *,
    session_id: str,
) -> TenantContext:
    """Load OTP/email bind for this guest session into TenantContext."""
    from app.identity.service import IdentityService

    user = await IdentityService(session, context).resolve_for_session(
        external_session_id=session_id,
        guest_user_id=context.user_id,
    )
    if user is None:
        return context
    enriched = TenantContext(
        tenant_id=context.tenant_id,
        user_id=context.user_id,
        role=context.role,
        clerk_org_id=context.clerk_org_id,
        scopes=context.scopes,
        principal_type=context.principal_type,
        verified_end_user_id=user.id,
        verified_email=user.email,
    )
    set_tenant_context(enriched)
    return enriched


def _attach_identity(runtime: Any, session: Any, context: TenantContext) -> None:
    from app.identity.tools import attach_identity_tools

    attach_identity_tools(runtime, session, context)


async def _rate_limit_public_chat(
    *,
    tenant_id: uuid.UUID,
    guest_user_id: str,
    client_host: str,
) -> None:
    settings = get_settings()
    per_guest = max(1, settings.public_chat_rate_limit_per_minute)
    per_ip = max(per_guest, settings.public_chat_rate_limit_per_minute * 3)
    await limiter.async_hit(f"public-chat:guest:{tenant_id}:{guest_user_id}", limit=per_guest)
    await limiter.async_hit(f"public-chat:ip:{client_host}", limit=per_ip)
    await limiter.async_acquire(
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
            await limiter.async_release(f"public-chat:concurrency:{tenant_id}")

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
    """Agents are not publicly callable — use team or workflow runs."""
    del request, tenant_slug, agent_slug, message, session_id, stream, x_guest_id
    raise HTTPException(
        status_code=404,
        detail="Public agent chat is not available. Use a published team or workflow.",
    )


@router.post("/t/{tenant_slug}/teams/{team_slug}/runs")
async def run_public_team(
    request: Request,
    tenant_slug: str,
    team_slug: str,
    message: str = Form(...),
    session_id: str = Form(...),
    stream: bool = Form(True),
    background: bool = Form(True),
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
    from app.agent_runtime.run_control import new_run_id

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
            await _rate_limit_public_chat(
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

            context = await _with_verified_identity(
                session, context, session_id=session_id
            )
            from app.billing.enforcement import require_credits_for_run

            await require_credits_for_run(session, context)
            _attach_identity(team, session, context)
            await session.commit()

            durable_user_id = runtime_user_id(context)
            durable_session_id = runtime_session_id(context, session_id)
            metadata = dict(getattr(team, "_saas_metadata", {}) or {})
            metadata["public_chat"] = True
            if context.verified_end_user_id is not None:
                metadata["verified_end_user_id"] = str(context.verified_end_user_id)
                metadata["verified_email"] = context.verified_email
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
                        session_state=getattr(team, "_saas_session_state", None),
                        background=background,
                        run_id=new_run_id() if background else None,
                        event_handler=lambda payload: _persist_runtime_event(
                            payload,
                            context=context,
                            trace_id=trace_id,
                            external_session_id=session_id,
                            initial_title=message[:255],
                            preview=False,
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
                await limiter.async_release(f"public-chat:concurrency:{tenant.id}")
            raise


@router.post("/t/{tenant_slug}/workflows/{workflow_slug}/runs")
async def run_public_workflow(
    request: Request,
    tenant_slug: str,
    workflow_slug: str,
    message: str = Form(...),
    session_id: str = Form(...),
    stream: bool = Form(True),
    background: bool = Form(True),
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
    from app.agent_runtime.run_control import new_run_id

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
            await _rate_limit_public_chat(
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

            context = await _with_verified_identity(
                session, context, session_id=session_id
            )
            from app.billing.enforcement import require_credits_for_run

            await require_credits_for_run(session, context)
            _attach_identity(workflow, session, context)
            await session.commit()

            durable_user_id = runtime_user_id(context)
            durable_session_id = runtime_session_id(context, session_id)
            metadata = dict(getattr(workflow, "_saas_metadata", {}) or {})
            metadata["public_chat"] = True
            if context.verified_end_user_id is not None:
                metadata["verified_end_user_id"] = str(context.verified_end_user_id)
                metadata["verified_email"] = context.verified_email
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
                        session_state=getattr(workflow, "_saas_session_state", None),
                        background=background,
                        run_id=new_run_id() if background else None,
                        event_handler=lambda payload: _persist_runtime_event(
                            payload,
                            context=context,
                            trace_id=trace_id,
                            external_session_id=session_id,
                            initial_title=message[:255],
                            preview=False,
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
                await limiter.async_release(f"public-chat:concurrency:{tenant.id}")
            raise


def _parse_last_event_index(
    last_event_index: int | None,
    last_event_id: str | None,
) -> int | None:
    if last_event_index is not None:
        return last_event_index
    if last_event_id is None or not str(last_event_id).strip():
        return None
    try:
        return int(str(last_event_id).strip())
    except ValueError:
        return None


@router.post("/t/{tenant_slug}/teams/{team_slug}/runs/{run_id}/resume")
async def resume_public_team(
    request: Request,
    tenant_slug: str,
    team_slug: str,
    run_id: str,
    session_id: str = Form(...),
    last_event_index: int | None = Form(None),
    x_guest_id: str | None = Header(default=None),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """Resume a background public team run after disconnect."""
    from app.agent_runtime.run_control import iter_resume_sse

    host = request.client.host if request.client else "anon"
    guest_user_id = _guest_user_id(x_guest_id, fallback_host=host)
    acquired = False
    resolved_index = _parse_last_event_index(last_event_index, last_event_id)

    async with _public_run_context(tenant_slug, guest_user_id=guest_user_id) as (
        session,
        tenant,
        context,
    ):
        try:
            await _rate_limit_public_chat(
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
                if existing is None:
                    raise LookupError("Session not found")
                if existing.user_id != context.user_id:
                    raise PermissionError("Session belongs to another user")
                if existing.target_type != "team" or existing.team_config_id != config.id:
                    raise ValueError("Session is pinned to another target")
                resolved_version_id = existing.team_version_id or config.published_version_id
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

            context = await _with_verified_identity(
                session, context, session_id=session_id
            )
            await session.commit()
            durable_user_id = runtime_user_id(context)
            durable_session_id = runtime_session_id(context, session_id)

            async def event_stream() -> AsyncIterator[bytes]:
                async for item in iter_resume_sse(
                    team,
                    run_id=run_id,
                    session_id=durable_session_id,
                    user_id=durable_user_id,
                    last_event_index=resolved_index,
                ):
                    yield item

            return _streaming_response(
                event_stream(), session_id=session_id, tenant_id=tenant.id
            )
        except Exception:
            if acquired:
                await limiter.async_release(f"public-chat:concurrency:{tenant.id}")
            raise


@router.post("/t/{tenant_slug}/workflows/{workflow_slug}/runs/{run_id}/resume")
async def resume_public_workflow(
    request: Request,
    tenant_slug: str,
    workflow_slug: str,
    run_id: str,
    session_id: str = Form(...),
    last_event_index: int | None = Form(None),
    x_guest_id: str | None = Header(default=None),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """Resume a background public workflow run after disconnect."""
    from app.agent_runtime.run_control import iter_resume_sse

    host = request.client.host if request.client else "anon"
    guest_user_id = _guest_user_id(x_guest_id, fallback_host=host)
    acquired = False
    resolved_index = _parse_last_event_index(last_event_index, last_event_id)

    async with _public_run_context(tenant_slug, guest_user_id=guest_user_id) as (
        session,
        tenant,
        context,
    ):
        try:
            await _rate_limit_public_chat(
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
                if existing is None:
                    raise LookupError("Session not found")
                if existing.user_id != context.user_id:
                    raise PermissionError("Session belongs to another user")
                if (
                    existing.target_type != "workflow"
                    or existing.workflow_config_id != config.id
                ):
                    raise ValueError("Session is pinned to another target")
                resolved_version_id = (
                    existing.workflow_version_id or config.published_version_id
                )
                workflow = await WorkflowFactoryService(
                    AgentFactoryService(session, context),
                    TeamFactoryService(AgentFactoryService(session, context)),
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

            context = await _with_verified_identity(
                session, context, session_id=session_id
            )
            await session.commit()
            durable_user_id = runtime_user_id(context)
            durable_session_id = runtime_session_id(context, session_id)

            async def event_stream() -> AsyncIterator[bytes]:
                async for item in iter_resume_sse(
                    workflow,
                    run_id=run_id,
                    session_id=durable_session_id,
                    user_id=durable_user_id,
                    last_event_index=resolved_index,
                ):
                    yield item

            return _streaming_response(
                event_stream(), session_id=session_id, tenant_id=tenant.id
            )
        except Exception:
            if acquired:
                await limiter.async_release(f"public-chat:concurrency:{tenant.id}")
            raise


@router.post("/t/{tenant_slug}/teams/{team_slug}/runs/{run_id}/cancel")
async def cancel_public_team(
    request: Request,
    tenant_slug: str,
    team_slug: str,
    run_id: str,
    session_id: str = Form(...),
    x_guest_id: str | None = Header(default=None),
) -> dict[str, Any]:
    """Cancel an in-flight public team run."""
    from app.agent_runtime.run_control import cancel_component_run

    host = request.client.host if request.client else "anon"
    guest_user_id = _guest_user_id(x_guest_id, fallback_host=host)

    async with _public_run_context(tenant_slug, guest_user_id=guest_user_id) as (
        session,
        _tenant,
        context,
    ):
        repo = TeamRepository(session, context)
        session_repo = SessionRepository(session, context)
        try:
            config = await repo.get_config_by_slug(team_slug)
            if config is None or config.published_version_id is None:
                raise LookupError("Published team not found")
            existing = await session_repo.get_by_external(session_id)
            if existing is None:
                raise LookupError("Session not found")
            if existing.user_id != context.user_id:
                raise PermissionError("Session belongs to another user")
            if existing.target_type != "team" or existing.team_config_id != config.id:
                raise ValueError("Session is pinned to another target")
            resolved_version_id = existing.team_version_id or config.published_version_id
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

        cancelled = await cancel_component_run(team, run_id)
        await session_repo.touch_run(session_id, run_id=run_id, status="cancelled")
        await session.commit()
        if not cancelled:
            raise HTTPException(
                status_code=404, detail="Run not found or already finished"
            )
        return {"ok": True, "run_id": run_id, "status": "cancelled"}


@router.post("/t/{tenant_slug}/workflows/{workflow_slug}/runs/{run_id}/cancel")
async def cancel_public_workflow(
    request: Request,
    tenant_slug: str,
    workflow_slug: str,
    run_id: str,
    session_id: str = Form(...),
    x_guest_id: str | None = Header(default=None),
) -> dict[str, Any]:
    """Cancel an in-flight public workflow run."""
    from app.agent_runtime.run_control import cancel_component_run

    host = request.client.host if request.client else "anon"
    guest_user_id = _guest_user_id(x_guest_id, fallback_host=host)

    async with _public_run_context(tenant_slug, guest_user_id=guest_user_id) as (
        session,
        _tenant,
        context,
    ):
        repo = WorkflowRepository(session, context)
        session_repo = SessionRepository(session, context)
        try:
            config = await repo.get_config_by_slug(workflow_slug)
            if config is None or config.published_version_id is None:
                raise LookupError("Published workflow not found")
            existing = await session_repo.get_by_external(session_id)
            if existing is None:
                raise LookupError("Session not found")
            if existing.user_id != context.user_id:
                raise PermissionError("Session belongs to another user")
            if (
                existing.target_type != "workflow"
                or existing.workflow_config_id != config.id
            ):
                raise ValueError("Session is pinned to another target")
            resolved_version_id = (
                existing.workflow_version_id or config.published_version_id
            )
            workflow = await WorkflowFactoryService(
                AgentFactoryService(session, context),
                TeamFactoryService(AgentFactoryService(session, context)),
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

        cancelled = await cancel_component_run(workflow, run_id)
        await session_repo.touch_run(session_id, run_id=run_id, status="cancelled")
        await session.commit()
        if not cancelled:
            raise HTTPException(
                status_code=404, detail="Run not found or already finished"
            )
        return {"ok": True, "run_id": run_id, "status": "cancelled"}
