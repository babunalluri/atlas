"""Mount tenant-scoped agents into a single AgentOS instance.

Isolation model (shared runtime + shared Postgres with RLS):
- Pros: lower ops cost, dynamic factories, one deploy unit, simpler migrations.
- Cons: larger blast radius for runtime bugs / noisy neighbors.
- Alternative: DB+runtime per tenant for stronger compliance cells later.

Product runs go through Atlas `/v1/...` and `/public/...` routes. Native AgentOS
factory routes (`/agents/tenant-agent`, `/teams/tenant-team`,
`/workflows/tenant-workflow`) and global Agno surfaces (`/schedules`,
`/approvals`, …) are blocked in middleware; Agno factories remain registered so
AgentOS can boot and share PostgresDb persistence.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agent_runtime.factory import (
    AgentFactoryService,
    RuntimeRequest,
    TeamFactoryService,
    TeamRuntimeRequest,
    WorkflowFactoryService,
    WorkflowRuntimeRequest,
    build_tenant_agent_from_request,
)
from app.agent_runtime.persistence import get_agno_db, runtime_session_id, runtime_user_id
from app.agent_runtime.run_control import new_run_id
from app.api import agents as agents_api
from app.api import approvals as approvals_api
from app.api import channels as channels_api
from app.api import credentials as credentials_api
from app.api import admin_vault as admin_vault_api
from app.api import user_vault as user_vault_api
from app.api import evals as evals_api
from app.api import health as health_api
from app.api import interfaces as interfaces_api
from app.api import knowledge as knowledge_api
from app.api import learnings as learnings_api
from app.api import mcp as mcp_api
from app.api import metrics as metrics_api
from app.api import billing as billing_api
from app.api import billing_webhooks as billing_webhooks_api
from app.api import notifications as notifications_api
from app.api import platform as platform_api
from app.api import onboarding as onboarding_api
from app.api import public as public_api
from app.api import public_channels as public_channels_api
from app.api import public_chat as public_chat_api
from app.api import public_email as public_email_api
from app.api import public_identity as public_identity_api
from app.api import sandbox_internal as sandbox_internal_api
from app.api import workspace as workspace_api
from app.api import schedules as schedules_api
from app.api import service_accounts as service_accounts_api
from app.api import sessions as sessions_api
from app.api import teams as teams_api
from app.api import team_access as team_access_api
from app.api import tools as tools_api
from app.api import traces as traces_api
from app.api import user_traces as user_traces_api
from app.api import workflows as workflows_api
from app.api import workflow_access as workflow_access_api
from app.api import customers as customers_api
from app.api import domains as domains_api
from app.api import desk as desk_api
from app.api import options_lab as options_lab_api
from app.api import signals as signals_api
from app.api import users as users_api
from app.auth.dependencies import require_tenant
from app.auth.middleware import TenantAuthMiddleware
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.rate_limit import RateLimitMiddleware
from app.core.settings import get_settings
from app.db.repositories import (
    AgentRepository,
    ApprovalRepository,
    MembershipRepository,
    SessionRepository,
    TeamRepository,
    WorkflowRepository,
)
from app.db.session import SessionFactory
from app.observability.repository import TraceRepository
from app.observability.tracing import redact
from app.scheduler.service import SchedulerWorker
from app.domains.signal_engine_worker import SignalEngineWorker
from app.tenancy.context import current_tenant, set_tenant_context

logger = get_logger(__name__)


async def _configure_redis_run_cancellation() -> None:
    """Use Agno's Redis cancellation manager when REDIS_URL is configured."""
    from app.core.redis_client import get_redis, redis_enabled

    if not redis_enabled():
        return
    async_client = await get_redis()
    if async_client is None:
        return
    try:
        from redis import Redis as SyncRedis
        from agno.run.cancel import set_cancellation_manager
        from agno.run.cancellation_management.redis_cancellation_manager import (
            RedisRunCancellationManager,
        )

        settings = get_settings()
        sync_client = SyncRedis.from_url(
            settings.redis_url.strip(),
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        set_cancellation_manager(
            RedisRunCancellationManager(
                redis_client=sync_client,
                async_redis_client=async_client,
            )
        )
        logger.info("agno_redis_run_cancellation_enabled")
    except Exception as exc:  # noqa: BLE001
        logger.warning("agno_redis_run_cancellation_unavailable", error=str(exc))


class FactoryInput(BaseModel):
    version_id: uuid.UUID
    preview: bool = False
    knowledge_base_id: uuid.UUID | None = None


class TeamFactoryInput(BaseModel):
    team_version_id: uuid.UUID
    preview: bool = False


class WorkflowFactoryInput(BaseModel):
    workflow_version_id: uuid.UUID
    preview: bool = False


def _try_build_agent_os(base_app: FastAPI) -> Any | None:
    settings = get_settings()
    try:
        from agno.os import AgentOS
    except ImportError:
        try:
            from agno.agentos import AgentOS  # type: ignore[attr-defined]
        except ImportError:
            logger.warning("agno AgentOS not available; using custom FastAPI runtime only")
            return None

    db = None
    try:
        db = get_agno_db()
    except Exception as exc:  # pragma: no cover - optional dependency path
        logger.warning("Agno PostgresDb unavailable", error=str(exc))

    agents: list[Any] = []
    teams: list[Any] = []
    workflows: list[Any] = []
    try:
        from agno.agent.factory import AgentFactory as AgnoAgentFactory
    except ImportError:
        AgnoAgentFactory = None  # type: ignore[assignment,misc]

    if AgnoAgentFactory is not None and db is not None:

        async def _factory(ctx: Any) -> Any:
            async with SessionFactory() as session:
                tenant = current_tenant()
                if session.bind and session.bind.dialect.name == "postgresql":
                    from sqlalchemy import text

                    await session.execute(
                        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                        {"tenant_id": str(tenant.tenant_id)},
                    )
                session.info["tenant_id"] = tenant.tenant_id
                agent = await build_tenant_agent_from_request(ctx, session)
                await session.commit()
                return agent

        agents.append(
            AgnoAgentFactory(
                id="tenant-agent",
                db=db,
                factory=_factory,
                name="Tenant Agent",
                description="Builds a tenant-scoped agent from verified JWT claims",
                input_schema=FactoryInput,
            )
        )
        try:
            from agno.team.factory import TeamFactory as AgnoTeamFactory

            async def _team_factory(ctx: Any) -> Any:
                tenant = current_tenant()
                factory_input = getattr(ctx, "input", None)
                if hasattr(factory_input, "model_dump"):
                    factory_input = factory_input.model_dump()
                factory_input = factory_input or {}
                team_version_id = factory_input.get("team_version_id")
                if not team_version_id:
                    raise ValueError("factory_input.team_version_id is required")
                async with SessionFactory() as session:
                    if session.bind and session.bind.dialect.name == "postgresql":
                        from sqlalchemy import text

                        await session.execute(
                            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                            {"tenant_id": str(tenant.tenant_id)},
                        )
                    session.info["tenant_id"] = tenant.tenant_id
                    service = AgentFactoryService(session, tenant)
                    session_id = getattr(ctx, "session_id", None) or str(uuid.uuid4())
                    team = await TeamFactoryService(service).create(
                        TeamRuntimeRequest(
                            version_id=uuid.UUID(str(team_version_id)),
                            session_id=session_id,
                            preview=bool(factory_input.get("preview", False)),
                        )
                    )
                    await session.commit()
                    return team

            teams.append(
                AgnoTeamFactory(
                    id="tenant-team",
                    db=db,
                    factory=_team_factory,
                    name="Tenant Team",
                    description="Builds a tenant-scoped team from published agent versions",
                    input_schema=TeamFactoryInput,
                )
            )
        except ImportError:
            logger.warning("Agno TeamFactory unavailable")
        try:
            from agno.workflow.factory import WorkflowFactory as AgnoWorkflowFactory

            async def _workflow_factory(ctx: Any) -> Any:
                tenant = current_tenant()
                factory_input = getattr(ctx, "input", None)
                if hasattr(factory_input, "model_dump"):
                    factory_input = factory_input.model_dump()
                factory_input = factory_input or {}
                workflow_version_id = factory_input.get("workflow_version_id")
                if not workflow_version_id:
                    raise ValueError("factory_input.workflow_version_id is required")
                async with SessionFactory() as session:
                    if session.bind and session.bind.dialect.name == "postgresql":
                        from sqlalchemy import text

                        await session.execute(
                            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                            {"tenant_id": str(tenant.tenant_id)},
                        )
                    session.info["tenant_id"] = tenant.tenant_id
                    service = AgentFactoryService(session, tenant)
                    workflow = await WorkflowFactoryService(service).create(
                        WorkflowRuntimeRequest(
                            version_id=uuid.UUID(str(workflow_version_id)),
                            session_id=getattr(ctx, "session_id", None) or str(uuid.uuid4()),
                            preview=bool(factory_input.get("preview", False)),
                        )
                    )
                    await session.commit()
                    return workflow

            workflows.append(
                AgnoWorkflowFactory(
                    id="tenant-workflow",
                    db=db,
                    factory=_workflow_factory,
                    name="Tenant Workflow",
                    description="Builds a tenant-scoped workflow with pinned component versions",
                    input_schema=WorkflowFactoryInput,
                )
            )
        except ImportError:
            logger.warning("Agno WorkflowFactory unavailable")
    else:
        # Register a lightweight prototype so AgentOS still boots when factories
        # are unavailable in the installed Agno version.
        try:
            from agno.agent import Agent
            from agno.models.openai import OpenAIChat

            agents.append(
                Agent(
                    id="tenant-agent",
                    name="Tenant Agent Placeholder",
                    model=OpenAIChat(id="gpt-4.1-mini"),
                    instructions="Placeholder agent. Use /v1/agents/tenant-agent/runs.",
                    db=db,
                )
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("Could not register placeholder agent", error=str(exc))
            return None

    kwargs: dict[str, Any] = {
        "id": settings.agent_os_id,
        "description": settings.app_name,
        "db": db,
        "agents": agents,
        "teams": teams,
        "workflows": workflows,
        "base_app": base_app,
        # Clerk JWT verification and tenant/role policy are enforced by the
        # outer middleware. Enabling AgentOS' second JWT verifier would require
        # exporting Clerk's rotating JWKS to a static local key file.
        "authorization": False,
        "tracing": True,
        "telemetry": False,
        "on_route_conflict": "preserve_base_app",
    }
    try:
        agent_os = AgentOS(**{k: v for k, v in kwargs.items() if v is not None})
    except TypeError:
        kwargs.pop("tracing", None)
        kwargs.pop("on_route_conflict", None)
        agent_os = AgentOS(**kwargs)
    return agent_os


async def _sse_from_agent(
    agent: Any,
    message: str,
    *,
    user_id: str,
    session_id: str,
    event_handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    wall_seconds: int | None = None,
    background: bool = False,
    run_id: str | None = None,
    session_state: dict[str, Any] | None = None,
) -> AsyncIterator[bytes]:
    from app.agent_runtime.run_control import iter_component_sse

    async for frame in iter_component_sse(
        agent,
        message,
        user_id=user_id,
        session_id=session_id,
        background=background,
        run_id=run_id,
        event_handler=event_handler,
        wall_seconds=wall_seconds,
        session_state=session_state,
    ):
        yield frame


def _event_payload(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        if "event" in event:
            return _normalize_event_name(event)
        return _normalize_event_name(
            {"event": event.get("type", "RunContent"), **event}
        )
    if hasattr(event, "to_dict"):
        data = event.to_dict()
        if isinstance(data, dict):
            name = data.get("event") or data.get("type") or "RunContent"
            return _normalize_event_name(
                {**data, "event": name, "original_event": name}
            )
    for attr in ("content", "delta", "message", "response"):
        value = getattr(event, attr, None)
        if isinstance(value, str) and value:
            return {"event": "RunContent", "content": value}
    if hasattr(event, "model_dump"):
        data = event.model_dump()
        name = data.get("event") or data.get("type") or "RunContent"
        return _normalize_event_name({**data, "event": name, "original_event": name})
    text = str(event)
    return {"event": "RunContent", "content": text}


def _flatten_workflow_requirements(step_requirements: list[Any]) -> list[dict[str, Any]]:
    """Expand workflow step (+ nested executor) requirements for approval bindings."""
    flattened: list[dict[str, Any]] = []
    for step in step_requirements:
        if hasattr(step, "to_dict"):
            step = step.to_dict()
        if not isinstance(step, dict):
            continue
        flattened.append(step)
        for nested in step.get("executor_requirements") or []:
            if hasattr(nested, "to_dict"):
                nested = nested.to_dict()
            if isinstance(nested, dict):
                flattened.append(nested)
    return flattened


def _normalize_event_name(payload: dict[str, Any]) -> dict[str, Any]:
    """Map Agno team/workflow event names onto the chat UI's expected set."""
    name = str(payload.get("event") or payload.get("type") or "RunContent")
    normalized = {
        "WorkflowStarted": "RunStarted",
        "WorkflowCompleted": "RunCompleted",
        "WorkflowError": "RunError",
        "WorkflowCancelled": "RunCancelled",
        "WorkflowPaused": "RunPaused",
        "StepOutput": "RunContent",
        # Team streaming uses TeamRunContent; chat only renders RunContent.
        "TeamRunContent": "RunContent",
        "TeamRunContentCompleted": "RunContentCompleted",
        "TeamRunCompleted": "RunCompleted",
        "TeamRunError": "RunError",
        "TeamRunCancelled": "RunCancelled",
        "TeamRunPaused": "RunPaused",
        "TeamRunStarted": "RunStarted",
    }.get(name, name)
    out = {**payload, "event": normalized}
    if normalized != name:
        out.setdefault("original_event", name)
    return out


async def _persist_runtime_event(
    payload: dict[str, Any],
    *,
    context: Any,
    trace_id: uuid.UUID,
    external_session_id: str,
    initial_title: str,
    preview: bool = False,
    scheduler: bool = False,
) -> dict[str, Any]:
    """Link native run state to the tenant product session and approval queue."""
    from sqlalchemy import text

    from app.billing.enforcement import record_run_billing

    async with SessionFactory() as session:
        if session.bind and session.bind.dialect.name == "postgresql":
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(context.tenant_id)},
            )
        session.info["tenant_id"] = context.tenant_id
        sessions = SessionRepository(session, context)
        await TraceRepository(session, context).record_event(trace_id, payload)
        event_name = str(payload.get("event") or "")
        status = {
            "RunPaused": "paused",
            "RunCompleted": "completed",
            "RunError": "error",
            "RunCancelled": "cancelled",
        }.get(event_name, "running")
        run_id = str(payload.get("run_id")) if payload.get("run_id") else None
        await sessions.touch_run(
            external_session_id,
            run_id=run_id,
            status=status,
            title=initial_title if event_name == "RunStarted" else None,
        )
        if event_name == "RunPaused" and run_id:
            conversation = await sessions.get_by_external(external_session_id)
            if conversation is not None:
                raw_requirements = payload.get("requirements") or []
                if not raw_requirements and payload.get("step_requirements"):
                    raw_requirements = _flatten_workflow_requirements(
                        payload.get("step_requirements") or []
                    )
                if not raw_requirements and payload.get("tools"):
                    raw_requirements = [
                        {
                            "id": item.get("tool_call_id"),
                            "tool_execution": item,
                        }
                        for item in payload["tools"]
                        if isinstance(item, dict)
                    ]
                approval_ids: list[str] = []
                for requirement in raw_requirements:
                    if not isinstance(requirement, dict):
                        continue
                    safe_requirement = redact(requirement)
                    approval = await ApprovalRepository(session, context).create_from_requirement(
                        conversation=conversation,
                        run_id=run_id,
                        requirement=safe_requirement,
                    )
                    approval_ids.append(str(approval.id))
                payload["approval_ids"] = approval_ids
        if event_name == "RunCompleted":
            await record_run_billing(
                session,
                context,
                payload,
                preview=preview,
                scheduler=scheduler,
            )
        await session.commit()
    return payload


async def _start_runtime_trace(
    *,
    context: Any,
    external_session_id: str,
    target_id: uuid.UUID,
    version_id: uuid.UUID,
    name: str,
    message: str,
    metadata: dict[str, Any],
) -> uuid.UUID:
    from sqlalchemy import text

    async with SessionFactory() as session:
        if session.bind and session.bind.dialect.name == "postgresql":
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(context.tenant_id)},
            )
        session.info["tenant_id"] = context.tenant_id
        conversation = await SessionRepository(session, context).get_by_external(
            external_session_id
        )
        if conversation is None:
            raise RuntimeError("Pinned conversation was not found for trace")
        trace = await TraceRepository(session, context).start(
            conversation=conversation,
            target_id=target_id,
            version_id=version_id,
            name=name,
            message=message,
            metadata=metadata,
        )
        await session.commit()
        return trace.id


async def _fail_runtime_trace(context: Any, trace_id: uuid.UUID, message: str) -> None:
    from sqlalchemy import text

    async with SessionFactory() as session:
        if session.bind and session.bind.dialect.name == "postgresql":
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(context.tenant_id)},
            )
        session.info["tenant_id"] = context.tenant_id
        await TraceRepository(session, context).fail(trace_id, message)
        await session.commit()


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        from app.core.redis_client import close_redis, get_redis
        from app.db.roles import assert_runtime_db_role_safe
        from app.db.session import engine

        await assert_runtime_db_role_safe(engine, settings)
        await get_redis()
        await _configure_redis_run_cancellation()
        worker = SchedulerWorker()
        signal_worker = SignalEngineWorker()
        if settings.scheduler_enabled and settings.environment.lower() != "test":
            worker.start()
        if settings.signal_engine_ticker_enabled and settings.environment.lower() != "test":
            signal_worker.start()
        try:
            yield
        finally:
            await signal_worker.stop()
            await worker.stop()
            await close_redis()

    base_app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    register_exception_handlers(base_app)
    base_app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    base_app.add_middleware(RateLimitMiddleware)
    base_app.add_middleware(TenantAuthMiddleware)

    base_app.include_router(health_api.router)
    base_app.include_router(agents_api.router)
    base_app.include_router(knowledge_api.router)
    base_app.include_router(approvals_api.router)
    base_app.include_router(credentials_api.router)
    base_app.include_router(user_vault_api.router)
    base_app.include_router(user_traces_api.router)
    base_app.include_router(admin_vault_api.router)
    base_app.include_router(channels_api.router)
    base_app.include_router(evals_api.router)
    base_app.include_router(learnings_api.router)
    base_app.include_router(teams_api.router)
    base_app.include_router(tools_api.router)
    base_app.include_router(sandbox_internal_api.router)
    base_app.include_router(public_api.router)
    base_app.include_router(public_chat_api.router)
    base_app.include_router(public_channels_api.router)
    base_app.include_router(public_email_api.router)
    base_app.include_router(public_identity_api.router)
    base_app.include_router(interfaces_api.router)
    base_app.include_router(onboarding_api.router)
    base_app.include_router(domains_api.router)
    base_app.include_router(signals_api.router)
    base_app.include_router(options_lab_api.router)
    base_app.include_router(desk_api.router)
    base_app.include_router(workspace_api.router)
    base_app.include_router(schedules_api.router)
    base_app.include_router(sessions_api.router)
    base_app.include_router(service_accounts_api.router)
    base_app.include_router(mcp_api.router)
    base_app.include_router(metrics_api.router)
    base_app.include_router(platform_api.router)
    base_app.include_router(traces_api.router)
    base_app.include_router(workflows_api.router)
    base_app.include_router(workflow_access_api.router)
    base_app.include_router(team_access_api.router)
    base_app.include_router(users_api.router)
    base_app.include_router(notifications_api.admin_router)
    base_app.include_router(notifications_api.me_router)
    base_app.include_router(billing_api.admin_router)
    base_app.include_router(billing_api.me_router)
    base_app.include_router(billing_webhooks_api.router)
    base_app.include_router(customers_api.router)

    @base_app.post("/v1/agents/tenant-agent/runs")
    async def run_tenant_agent(
        request: Request,
        message: str = Form(...),
        version_id: str | None = Form(None),
        agent_config_id: str | None = Form(None),
        session_id: str = Form(...),
        preview: bool = Form(False),
        stream: bool = Form(True),
        background: bool = Form(False),
        authorization: str | None = Header(default=None),
    ) -> StreamingResponse:
        """Tenant-scoped streaming run endpoint.

        Product traffic uses this `/v1/...` path (and public chat), not native
        AgentOS `/agents/tenant-agent/runs`, which middleware returns 404 for
        because native AgentOS storage is not tenant-keyed.
        """
        context = getattr(request.state, "tenant", None)
        if context is None:
            context = await require_tenant(
                request,
                authorization=authorization,
                x_platform_tenant_id=request.headers.get("x-platform-tenant-id"),
                settings=settings,
            )
            set_tenant_context(context)

        if preview and not context.can_administer():
            raise HTTPException(status_code=403, detail="Preview runs require admin role")
        durable_user_id = runtime_user_id(context)
        durable_session_id = runtime_session_id(context, session_id)

        async with SessionFactory() as session:
            if session.bind and session.bind.dialect.name == "postgresql":
                from sqlalchemy import text

                await session.execute(
                    text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(context.tenant_id)},
                )
            session.info["tenant_id"] = context.tenant_id
            from app.billing.enforcement import require_credits_for_run

            await require_credits_for_run(session, context, preview=preview)
            factory = AgentFactoryService(session, context)
            session_repo = SessionRepository(session, context)
            try:
                existing = await session_repo.get_by_external(session_id)
                if existing is not None:
                    if existing.user_id != context.user_id:
                        raise PermissionError("Session belongs to another user")
                    if existing.target_type != "agent":
                        raise ValueError("Session is pinned to a team")
                    if agent_config_id and existing.agent_config_id != uuid.UUID(agent_config_id):
                        raise ValueError("Session is pinned to another agent")
                    resolved_version_id = str(existing.agent_version_id)
                else:
                    resolved_version_id = version_id
                if resolved_version_id is None and agent_config_id is not None:
                    repo = AgentRepository(session, context)
                    config = await repo.get_config(uuid.UUID(agent_config_id))
                    if config is None:
                        raise LookupError("Agent configuration not found")
                    if preview:
                        draft = await repo.get_latest_draft(config.id)
                        resolved_version_id = str(draft.id) if draft else None
                    else:
                        resolved_version_id = (
                            str(config.published_version_id)
                            if config.published_version_id
                            else None
                        )
                if resolved_version_id is None:
                    raise ValueError("version_id or agent_config_id is required")
                agent = await factory.create(
                    RuntimeRequest(
                        version_id=uuid.UUID(resolved_version_id),
                        session_id=session_id,
                        preview=preview,
                    )
                )
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            # Persist version pinning before the response starts. The request
            # session closes when this handler returns, while AgentOS uses its
            # own durable Postgres provider during the streamed run.
            await session.commit()
            agent_metadata = getattr(agent, "_saas_metadata", {})
            trace_id = await _start_runtime_trace(
                context=context,
                external_session_id=session_id,
                target_id=uuid.UUID(str(agent_metadata["agent_id"])),
                version_id=uuid.UUID(resolved_version_id),
                name="Agent run",
                message=message,
                metadata=agent_metadata,
            )

            if not stream:
                # Non-streaming convenience path for tests.
                content = ""
                async for chunk in _sse_from_agent(
                    agent,
                    message,
                    user_id=durable_user_id,
                    session_id=durable_session_id,
                    session_state=getattr(agent, "_saas_session_state", None),
                    event_handler=lambda payload: _persist_runtime_event(
                        payload,
                        context=context,
                        trace_id=trace_id,
                        external_session_id=session_id,
                        initial_title=message[:255],
                        preview=preview,
                    ),
                ):
                    if chunk.startswith(b"data: "):
                        payload = json.loads(chunk[6:])
                        if payload.get("event") == "RunContent":
                            content += payload.get("content", "")
                content_event = json.dumps({"event": "RunContent", "content": content})
                return StreamingResponse(  # type: ignore[return-value]
                    iter(
                        [
                            f"data: {content_event}\n\n".encode(),
                            b'data: {"event":"RunCompleted"}\n\n',
                        ]
                    ),
                    media_type="text/event-stream",
                )

            async def event_stream() -> AsyncIterator[bytes]:
                try:
                    async for item in _sse_from_agent(
                        agent,
                        message,
                        user_id=durable_user_id,
                        session_id=durable_session_id,
                        session_state=getattr(agent, "_saas_session_state", None),
                        background=background,
                        run_id=new_run_id() if background else None,
                        event_handler=lambda payload: _persist_runtime_event(
                            payload,
                            context=context,
                            trace_id=trace_id,
                            external_session_id=session_id,
                            initial_title=message[:255],
                            preview=preview,
                        ),
                    ):
                        yield item
                except Exception as exc:
                    logger.exception("agent_run_failed", error=str(exc))
                    await _fail_runtime_trace(context, trace_id, "Agent run failed")
                    yield b'data: {"event":"RunError","error":"Agent run failed"}\n\n'

            return StreamingResponse(event_stream(), media_type="text/event-stream")

    @base_app.post("/v1/teams/tenant-team/runs")
    async def run_tenant_team(
        request: Request,
        message: str = Form(...),
        team_version_id: str | None = Form(None),
        team_config_id: str | None = Form(None),
        session_id: str = Form(...),
        preview: bool = Form(False),
        stream: bool = Form(True),
        background: bool = Form(False),
        authorization: str | None = Header(default=None),
    ) -> StreamingResponse:
        context = getattr(request.state, "tenant", None)
        if context is None:
            context = await require_tenant(
                request,
                authorization=authorization,
                x_platform_tenant_id=request.headers.get("x-platform-tenant-id"),
                settings=settings,
            )
            set_tenant_context(context)
        if preview and not context.can_administer():
            raise HTTPException(status_code=403, detail="Preview runs require admin role")

        async with SessionFactory() as session:
            if session.bind and session.bind.dialect.name == "postgresql":
                from sqlalchemy import text

                await session.execute(
                    text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(context.tenant_id)},
                )
            session.info["tenant_id"] = context.tenant_id
            from app.billing.enforcement import require_credits_for_run

            await require_credits_for_run(session, context, preview=preview)
            repo = TeamRepository(session, context)
            session_repo = SessionRepository(session, context)
            try:
                existing = await session_repo.get_by_external(session_id)
                if existing is not None:
                    if existing.user_id != context.user_id:
                        raise PermissionError("Session belongs to another user")
                    if existing.target_type != "team":
                        raise ValueError("Session is pinned to an agent")
                    if team_config_id and existing.team_config_id != uuid.UUID(team_config_id):
                        raise ValueError("Session is pinned to another team")
                    resolved_version_id = str(existing.team_version_id)
                else:
                    resolved_version_id = team_version_id
                if resolved_version_id is None and team_config_id is not None:
                    config = await repo.get_config(uuid.UUID(team_config_id))
                    if config is None:
                        raise LookupError("Team configuration not found")
                    if preview:
                        draft = await repo.get_latest_draft(config.id)
                        resolved_version_id = str(draft.id) if draft else None
                    else:
                        resolved_version_id = (
                            str(config.published_version_id)
                            if config.published_version_id
                            else None
                        )
                if resolved_version_id is None:
                    raise ValueError("team_version_id or team_config_id is required")
                version_uuid = uuid.UUID(resolved_version_id)
                version = await repo.get_version(version_uuid, allow_draft=preview)
                if version is None:
                    raise LookupError("Team version not found")
                if not context.can_administer():
                    membership = await MembershipRepository(
                        session, context
                    ).get_by_user_id(context.user_id)
                    if membership is not None and not membership.is_active:
                        raise PermissionError("User account is inactive")
                    if not await repo.is_assigned(
                        version.team_config_id, context.user_id
                    ):
                        raise PermissionError("Team is not assigned to this user")
                team = await TeamFactoryService(AgentFactoryService(session, context)).create(
                    TeamRuntimeRequest(
                        version_id=version_uuid,
                        session_id=session_id,
                        preview=preview,
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
            team_metadata = getattr(team, "_saas_metadata", {})
            trace_id = await _start_runtime_trace(
                context=context,
                external_session_id=session_id,
                target_id=uuid.UUID(str(team_metadata["team_id"])),
                version_id=version_uuid,
                name="Team run",
                message=message,
                metadata=team_metadata,
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
                            preview=preview,
                        ),
                    ):
                        yield item
                except Exception as exc:
                    logger.exception("team_run_failed", error=str(exc))
                    await _fail_runtime_trace(context, trace_id, "Team run failed")
                    yield b'data: {"event":"RunError","error":"Team run failed"}\n\n'

            return StreamingResponse(event_stream(), media_type="text/event-stream")

    @base_app.post("/v1/workflows/tenant-workflow/runs")
    async def run_tenant_workflow(
        request: Request,
        message: str = Form(...),
        workflow_version_id: str | None = Form(None),
        workflow_config_id: str | None = Form(None),
        session_id: str = Form(...),
        preview: bool = Form(False),
        stream: bool = Form(True),
        background: bool = Form(False),
        authorization: str | None = Header(default=None),
    ) -> StreamingResponse:
        context = getattr(request.state, "tenant", None)
        if context is None:
            context = await require_tenant(
                request,
                authorization=authorization,
                x_platform_tenant_id=request.headers.get("x-platform-tenant-id"),
                settings=settings,
            )
            set_tenant_context(context)
        if preview and not context.can_administer():
            raise HTTPException(status_code=403, detail="Preview runs require admin role")

        async with SessionFactory() as session:
            if session.bind and session.bind.dialect.name == "postgresql":
                from sqlalchemy import text

                await session.execute(
                    text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(context.tenant_id)},
                )
            session.info["tenant_id"] = context.tenant_id
            from app.billing.enforcement import require_credits_for_run

            await require_credits_for_run(session, context, preview=preview)
            repo = WorkflowRepository(session, context)
            session_repo = SessionRepository(session, context)
            try:
                existing = await session_repo.get_by_external(session_id)
                if existing is not None:
                    if existing.user_id != context.user_id:
                        raise PermissionError("Session belongs to another user")
                    if existing.target_type != "workflow":
                        raise ValueError("Session is pinned to another target")
                    if (
                        workflow_config_id
                        and existing.workflow_config_id != uuid.UUID(workflow_config_id)
                    ):
                        raise ValueError("Session is pinned to another workflow")
                    resolved_version_id = str(existing.workflow_version_id)
                else:
                    resolved_version_id = workflow_version_id
                if resolved_version_id is None and workflow_config_id is not None:
                    config = await repo.get_config(uuid.UUID(workflow_config_id))
                    if config is None:
                        raise LookupError("Workflow configuration not found")
                    if preview:
                        draft = await repo.get_latest_draft(config.id)
                        resolved_version_id = str(draft.id) if draft else None
                    else:
                        resolved_version_id = (
                            str(config.published_version_id)
                            if config.published_version_id
                            else None
                        )
                if resolved_version_id is None:
                    raise ValueError(
                        "workflow_version_id or workflow_config_id is required"
                    )
                version_uuid = uuid.UUID(resolved_version_id)
                version = await repo.get_version(version_uuid, allow_draft=preview)
                if version is None:
                    raise LookupError("Workflow version not found")
                if not context.can_administer():
                    membership = await MembershipRepository(
                        session, context
                    ).get_by_user_id(context.user_id)
                    if membership is not None and not membership.is_active:
                        raise PermissionError("User account is inactive")
                    if not await repo.is_assigned(
                        version.workflow_config_id, context.user_id
                    ):
                        raise PermissionError("Workflow is not assigned to this user")
                workflow = await WorkflowFactoryService(
                    AgentFactoryService(session, context)
                ).create(
                    WorkflowRuntimeRequest(
                        version_id=version_uuid,
                        session_id=session_id,
                        preview=preview,
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
            metadata = getattr(workflow, "_saas_metadata", {})
            trace_id = await _start_runtime_trace(
                context=context,
                external_session_id=session_id,
                target_id=uuid.UUID(str(metadata["workflow_id"])),
                version_id=version_uuid,
                name="Workflow run",
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
                            preview=preview,
                        ),
                    ):
                        yield item
                except Exception as exc:
                    logger.exception("workflow_run_failed", error=str(exc))
                    await _fail_runtime_trace(context, trace_id, "Workflow run failed")
                    yield b'data: {"event":"RunError","error":"Workflow run failed"}\n\n'

            if not stream:
                logger.info("workflow_non_stream_requested", workflow_id=workflow_config_id)
            return StreamingResponse(event_stream(), media_type="text/event-stream")

    @base_app.post("/api/v1/public/runs")
    async def run_public_workflow_team(
        request: Request,
        message: str = Form(...),
        workflow_id: str = Form(...),
        team_id: str = Form(...),
        session_id: str | None = Form(None),
        new_session: bool = Form(False),
        stream: bool = Form(True),
        background: bool = Form(False),
        authorization: str | None = Header(default=None),
    ) -> StreamingResponse:
        """Run a team that is a published step of a workflow, keyed by org secret.

        Session handling:
        - Omit ``session_id`` (or pass ``new_session=true``) to start a conversation;
          the server mints an id and returns it via ``X-Session-Id`` plus a
          ``SessionStarted`` SSE event.
        - Pass a prior ``session_id`` to continue that conversation with the same
          workflow/team pair.
        """
        context = getattr(request.state, "tenant", None)
        if context is None:
            context = await require_tenant(
                request,
                authorization=authorization,
                x_platform_tenant_id=request.headers.get("x-platform-tenant-id"),
                settings=settings,
            )
            set_tenant_context(context)

        message = message.strip()
        if not message:
            raise HTTPException(status_code=400, detail="message must not be empty")

        provided_session = (session_id or "").strip() or None
        if new_session or provided_session is None:
            external_session_id = str(uuid.uuid4())
        else:
            external_session_id = provided_session

        async with SessionFactory() as session:
            if session.bind and session.bind.dialect.name == "postgresql":
                from sqlalchemy import text

                await session.execute(
                    text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(context.tenant_id)},
                )
            session.info["tenant_id"] = context.tenant_id
            from app.billing.enforcement import require_credits_for_run

            await require_credits_for_run(session, context)
            workflows = WorkflowRepository(session, context)
            teams = TeamRepository(session, context)
            session_repo = SessionRepository(session, context)
            try:
                workflow_uuid = uuid.UUID(workflow_id)
                team_uuid = uuid.UUID(team_id)
                team_version_id = await workflows.resolve_published_team_step(
                    workflow_uuid, team_uuid
                )
                team_config = await teams.get_config(team_uuid)
                if team_config is None or team_config.published_version_id is None:
                    raise LookupError("Published team not found")

                existing = await session_repo.get_by_external(external_session_id)
                if new_session and existing is not None:
                    external_session_id = str(uuid.uuid4())
                    existing = await session_repo.get_by_external(external_session_id)

                if existing is not None:
                    if existing.user_id != context.user_id:
                        raise PermissionError("Session belongs to another user")
                    if existing.target_type != "team":
                        raise ValueError("Session is pinned to another target")
                    if existing.team_config_id != team_uuid:
                        raise ValueError(
                            "Session is pinned to another team; omit session_id "
                            "or pass new_session=true to start a new conversation"
                        )
                    resolved_version_id = existing.team_version_id
                    if resolved_version_id is None:
                        raise ValueError("Session is missing a pinned team version")
                    session_started_new = False
                else:
                    resolved_version_id = team_version_id
                    session_started_new = True

                team = await TeamFactoryService(AgentFactoryService(session, context)).create(
                    TeamRuntimeRequest(
                        version_id=resolved_version_id,
                        session_id=external_session_id,
                        preview=False,
                    )
                )
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            except (ValueError, TypeError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            await session.commit()

            durable_user_id = runtime_user_id(context)
            durable_session_id = runtime_session_id(context, external_session_id)
            team_metadata = dict(getattr(team, "_saas_metadata", {}) or {})
            team_metadata["workflow_id"] = workflow_id
            team_metadata["public_api"] = True
            team_metadata["session_id"] = external_session_id
            trace_id = await _start_runtime_trace(
                context=context,
                external_session_id=external_session_id,
                target_id=uuid.UUID(str(team_metadata["team_id"])),
                version_id=resolved_version_id,
                name="Public API team run",
                message=message,
                metadata=team_metadata,
            )

            async def event_stream() -> AsyncIterator[bytes]:
                started = {
                    "event": "SessionStarted",
                    "session_id": external_session_id,
                    "workflow_id": workflow_id,
                    "team_id": team_id,
                    "new_session": session_started_new,
                }
                yield f"data: {json.dumps(started)}\n\n".encode()
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
                            external_session_id=external_session_id,
                            initial_title=message[:255],
                            preview=False,
                        ),
                    ):
                        yield item
                except Exception as exc:
                    logger.exception("public_api_team_run_failed", error=str(exc))
                    await _fail_runtime_trace(
                        context, trace_id, "Public API team run failed"
                    )
                    yield b'data: {"event":"RunError","error":"Team run failed"}\n\n'

            if not stream:
                logger.info(
                    "public_api_non_stream_requested",
                    workflow_id=workflow_id,
                    team_id=team_id,
                )
            return StreamingResponse(
                event_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "X-Session-Id": external_session_id,
                },
            )

    async def _resolve_tenant_for_run(
        request: Request, authorization: str | None
    ) -> Any:
        context = getattr(request.state, "tenant", None)
        if context is None:
            context = await require_tenant(
                request,
                authorization=authorization,
                x_platform_tenant_id=request.headers.get("x-platform-tenant-id"),
                settings=settings,
            )
            set_tenant_context(context)
        return context

    @base_app.post("/v1/agents/tenant-agent/runs/{run_id}/resume")
    @base_app.post("/v1/teams/tenant-team/runs/{run_id}/resume")
    @base_app.post("/v1/workflows/tenant-workflow/runs/{run_id}/resume")
    async def resume_tenant_run(
        request: Request,
        run_id: str,
        session_id: str = Form(...),
        last_event_index: int | None = Form(None),
        agent_config_id: str | None = Form(None),
        team_config_id: str | None = Form(None),
        workflow_config_id: str | None = Form(None),
        version_id: str | None = Form(None),
        preview: bool = Form(False),
        authorization: str | None = Header(default=None),
    ) -> StreamingResponse:
        context = await _resolve_tenant_for_run(request, authorization)
        path = request.url.path
        durable_user_id = runtime_user_id(context)
        durable_session_id = runtime_session_id(context, session_id)
        async with SessionFactory() as session:
            if session.bind and session.bind.dialect.name == "postgresql":
                from sqlalchemy import text

                await session.execute(
                    text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(context.tenant_id)},
                )
            session.info["tenant_id"] = context.tenant_id
            try:
                if "/agents/" in path:
                    if not agent_config_id and not version_id:
                        raise ValueError("agent_config_id or version_id is required")
                    repo = AgentRepository(session, context)
                    resolved = version_id
                    if resolved is None and agent_config_id:
                        config = await repo.get_config(uuid.UUID(agent_config_id))
                        if config is None or config.published_version_id is None:
                            raise LookupError("Agent configuration not found")
                        resolved = str(config.published_version_id)
                    component = await AgentFactoryService(session, context).create(
                        RuntimeRequest(
                            version_id=uuid.UUID(resolved),
                            session_id=session_id,
                            preview=preview,
                        )
                    )
                elif "/teams/" in path:
                    if not team_config_id and not version_id:
                        raise ValueError("team_config_id or version_id is required")
                    repo = TeamRepository(session, context)
                    resolved = version_id
                    if resolved is None and team_config_id:
                        config = await repo.get_config(uuid.UUID(team_config_id))
                        if config is None or config.published_version_id is None:
                            raise LookupError("Team configuration not found")
                        resolved = str(config.published_version_id)
                    component = await TeamFactoryService(
                        AgentFactoryService(session, context)
                    ).create(
                        TeamRuntimeRequest(
                            version_id=uuid.UUID(resolved),
                            session_id=session_id,
                            preview=preview,
                        )
                    )
                else:
                    if not workflow_config_id and not version_id:
                        raise ValueError("workflow_config_id or version_id is required")
                    repo = WorkflowRepository(session, context)
                    resolved = version_id
                    if resolved is None and workflow_config_id:
                        config = await repo.get_config(uuid.UUID(workflow_config_id))
                        if config is None or config.published_version_id is None:
                            raise LookupError("Workflow configuration not found")
                        resolved = str(config.published_version_id)
                    component = await WorkflowFactoryService(
                        AgentFactoryService(session, context),
                        TeamFactoryService(AgentFactoryService(session, context)),
                    ).create(
                        WorkflowRuntimeRequest(
                            version_id=uuid.UUID(resolved),
                            session_id=session_id,
                            preview=preview,
                        )
                    )
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            await session.commit()

            async def event_stream() -> AsyncIterator[bytes]:
                async for item in iter_resume_sse(
                    component,
                    run_id=run_id,
                    session_id=durable_session_id,
                    user_id=durable_user_id,
                    last_event_index=last_event_index,
                ):
                    yield item

            return StreamingResponse(event_stream(), media_type="text/event-stream")

    @base_app.post("/v1/agents/tenant-agent/runs/{run_id}/cancel")
    @base_app.post("/v1/teams/tenant-team/runs/{run_id}/cancel")
    @base_app.post("/v1/workflows/tenant-workflow/runs/{run_id}/cancel")
    async def cancel_tenant_run(
        request: Request,
        run_id: str,
        session_id: str = Form(...),
        agent_config_id: str | None = Form(None),
        team_config_id: str | None = Form(None),
        workflow_config_id: str | None = Form(None),
        version_id: str | None = Form(None),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        context = await _resolve_tenant_for_run(request, authorization)
        path = request.url.path
        async with SessionFactory() as session:
            if session.bind and session.bind.dialect.name == "postgresql":
                from sqlalchemy import text

                await session.execute(
                    text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(context.tenant_id)},
                )
            session.info["tenant_id"] = context.tenant_id
            try:
                if "/agents/" in path:
                    resolved = version_id
                    if resolved is None and agent_config_id:
                        config = await AgentRepository(session, context).get_config(
                            uuid.UUID(agent_config_id)
                        )
                        if config is None or config.published_version_id is None:
                            raise LookupError("Agent configuration not found")
                        resolved = str(config.published_version_id)
                    if resolved is None:
                        raise ValueError("version_id or agent_config_id is required")
                    component = await AgentFactoryService(session, context).create(
                        RuntimeRequest(
                            version_id=uuid.UUID(resolved),
                            session_id=session_id,
                            preview=False,
                        )
                    )
                elif "/teams/" in path:
                    resolved = version_id
                    if resolved is None and team_config_id:
                        config = await TeamRepository(session, context).get_config(
                            uuid.UUID(team_config_id)
                        )
                        if config is None or config.published_version_id is None:
                            raise LookupError("Team configuration not found")
                        resolved = str(config.published_version_id)
                    if resolved is None:
                        raise ValueError("version_id or team_config_id is required")
                    component = await TeamFactoryService(
                        AgentFactoryService(session, context)
                    ).create(
                        TeamRuntimeRequest(
                            version_id=uuid.UUID(resolved),
                            session_id=session_id,
                            preview=False,
                        )
                    )
                else:
                    resolved = version_id
                    if resolved is None and workflow_config_id:
                        config = await WorkflowRepository(session, context).get_config(
                            uuid.UUID(workflow_config_id)
                        )
                        if config is None or config.published_version_id is None:
                            raise LookupError("Workflow configuration not found")
                        resolved = str(config.published_version_id)
                    if resolved is None:
                        raise ValueError("version_id or workflow_config_id is required")
                    component = await WorkflowFactoryService(
                        AgentFactoryService(session, context),
                        TeamFactoryService(AgentFactoryService(session, context)),
                    ).create(
                        WorkflowRuntimeRequest(
                            version_id=uuid.UUID(resolved),
                            session_id=session_id,
                            preview=False,
                        )
                    )
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            cancelled = await cancel_component_run(component, run_id)
            await SessionRepository(session, context).touch_run(
                session_id, run_id=run_id, status="cancelled"
            )
            await session.commit()
            if not cancelled:
                raise HTTPException(
                    status_code=404, detail="Run not found or already finished"
                )
            return {"ok": True, "run_id": run_id, "status": "cancelled"}

    agent_os = _try_build_agent_os(base_app)
    if agent_os is not None:
        app = agent_os.get_app()
        # Keep a handle for tests / operational tooling.
        app.state.agent_os = agent_os
        return app
    return base_app
