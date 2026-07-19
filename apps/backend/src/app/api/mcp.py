"""Tenant-safe Atlas MCP gateway.

Agno's native MCP surface reads from the process-wide AgentOS registry and
storage. Atlas deliberately exposes a smaller protocol surface backed only by
tenant-scoped repositories and factories.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.factory import (
    AgentFactoryService,
    RuntimeRequest,
    TeamFactoryService,
    TeamRuntimeRequest,
    WorkflowFactoryService,
    WorkflowRuntimeRequest,
)
from app.agent_runtime.persistence import runtime_session_id, runtime_user_id
from app.auth.dependencies import require_tenant
from app.db.models import TenantMcpSettings
from app.db.repositories import (
    AgentRepository,
    SessionRepository,
    TeamRepository,
    WorkflowRepository,
)
from app.db.session import tenant_session
from app.tenancy.context import TenantContext

router = APIRouter(tags=["mcp"])
Context = Annotated[TenantContext, Depends(require_tenant)]
TenantSession = Annotated[AsyncSession, Depends(tenant_session)]

PROTOCOL_VERSION = "2025-03-26"


class McpSettingsIn(BaseModel):
    enabled: bool


class JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: str | int | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


def _settings_out(row: TenantMcpSettings | None) -> dict[str, Any]:
    return {
        "enabled": bool(row and row.enabled),
        "status": "ready" if row and row.enabled else "disabled",
        "endpoint": "/mcp",
        "protocol_version": PROTOCOL_VERSION,
        "implementation": "atlas_gateway",
        "required_scopes": ["mcp:access", "mcp:read", "mcp:run", "mcp:sessions:read"],
        "supports": [
            "list agents, teams, and workflows",
            "run published agents, teams, and workflows",
            "list tenant-accessible sessions",
        ],
        "limitations": [
            "Run cancellation is not exposed because the runtime has no safe cancellation API.",
            "Paused HITL runs continue through the existing approvals API.",
        ],
    }


async def _get_settings(
    session: AsyncSession, context: TenantContext
) -> TenantMcpSettings | None:
    return await session.scalar(
        select(TenantMcpSettings).where(
            TenantMcpSettings.tenant_id == context.tenant_id
        )
    )


def _require_scope(context: TenantContext, scope: str) -> None:
    if context.can_administer():
        return
    if not context.has_scope(scope):
        raise HTTPException(status_code=403, detail=f"{scope} scope is required")


@router.get("/admin/mcp")
async def get_mcp_settings(context: Context, session: TenantSession) -> dict[str, Any]:
    if not context.can_administer():
        raise HTTPException(status_code=403, detail="Tenant administrator role required")
    return _settings_out(await _get_settings(session, context))


@router.patch("/admin/mcp")
async def update_mcp_settings(
    payload: McpSettingsIn,
    context: Context,
    session: TenantSession,
) -> dict[str, Any]:
    if not context.can_administer():
        raise HTTPException(status_code=403, detail="Tenant administrator role required")
    row = await _get_settings(session, context)
    if row is None:
        row = TenantMcpSettings(
            id=uuid.uuid4(),
            tenant_id=context.tenant_id,
            enabled=payload.enabled,
            updated_by=context.user_id,
        )
        session.add(row)
    else:
        row.enabled = payload.enabled
        row.updated_by = context.user_id
    await session.flush()
    return _settings_out(row)


def _tools() -> list[dict[str, Any]]:
    target_properties = {
        "config_id": {
            "type": "string",
            "format": "uuid",
            "description": "Atlas configuration ID from the corresponding list tool.",
        },
        "message": {"type": "string", "minLength": 1},
        "session_id": {
            "type": "string",
            "description": "Optional caller-chosen session key, scoped server-side to this tenant.",
        },
    }
    run_schema = {
        "type": "object",
        "properties": target_properties,
        "required": ["config_id", "message"],
        "additionalProperties": False,
    }
    return [
        {
            "name": "atlas_list_agents",
            "description": "List published agents visible to the authenticated tenant.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "atlas_list_teams",
            "description": "List published teams visible to the authenticated tenant.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "atlas_list_workflows",
            "description": "List published workflows visible to the authenticated tenant.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "atlas_run_agent",
            "description": "Run a published tenant agent. Requires mcp:run.",
            "inputSchema": run_schema,
        },
        {
            "name": "atlas_run_team",
            "description": "Run a published tenant team. Requires mcp:run.",
            "inputSchema": run_schema,
        },
        {
            "name": "atlas_run_workflow",
            "description": "Run a published tenant workflow. Requires mcp:run.",
            "inputSchema": run_schema,
        },
        {
            "name": "atlas_list_sessions",
            "description": (
                "List sessions accessible to this principal. Requires mcp:sessions:read."
            ),
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    ]


def _resource(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "slug": row.slug,
        "published_version_id": (
            str(row.published_version_id) if row.published_version_id else None
        ),
    }


def _session(row: Any) -> dict[str, Any]:
    return {
        "id": row.external_session_id,
        "title": row.title,
        "target_type": row.target_type,
        "user_id": row.user_id,
        "status": row.status,
        "last_run_id": row.last_run_id,
        "updated_at": row.updated_at.isoformat(),
    }


async def _run(
    name: str,
    arguments: dict[str, Any],
    session: AsyncSession,
    context: TenantContext,
) -> dict[str, Any]:
    try:
        config_id = uuid.UUID(str(arguments["config_id"]))
        message = str(arguments["message"]).strip()
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("config_id must be a UUID and message is required") from exc
    if not message:
        raise ValueError("message must not be empty")
    session_id = str(arguments.get("session_id") or uuid.uuid4())
    factory = AgentFactoryService(session, context)

    if name == "atlas_run_agent":
        agent_config = await AgentRepository(session, context).get_config(config_id)
        if agent_config is None or agent_config.published_version_id is None:
            raise LookupError("Published agent not found")
        component = await factory.create(
            RuntimeRequest(agent_config.published_version_id, session_id)
        )
    elif name == "atlas_run_team":
        team_config = await TeamRepository(session, context).get_config(config_id)
        if team_config is None or team_config.published_version_id is None:
            raise LookupError("Published team not found")
        component = await TeamFactoryService(factory).create(
            TeamRuntimeRequest(team_config.published_version_id, session_id)
        )
    else:
        workflow_config = await WorkflowRepository(session, context).get_config(config_id)
        if workflow_config is None or workflow_config.published_version_id is None:
            raise LookupError("Published workflow not found")
        component = await WorkflowFactoryService(factory).create(
            WorkflowRuntimeRequest(workflow_config.published_version_id, session_id)
        )

    await session.commit()
    result = await component.arun(
        message,
        user_id=runtime_user_id(context),
        session_id=runtime_session_id(context, session_id),
        stream=False,
    )
    if hasattr(result, "to_dict"):
        output = result.to_dict()
    elif isinstance(result, dict):
        output = result
    else:
        output = {"content": str(getattr(result, "content", result))}
    return {"session_id": session_id, "result": output}


async def _call_tool(
    name: str,
    arguments: dict[str, Any],
    session: AsyncSession,
    context: TenantContext,
) -> Any:
    if name == "atlas_list_agents":
        _require_scope(context, "mcp:read")
        agent_rows = await AgentRepository(session, context).list_configs()
        return [_resource(row) for row in agent_rows if row.published_version_id]
    if name == "atlas_list_teams":
        _require_scope(context, "mcp:read")
        team_rows = await TeamRepository(session, context).list_configs()
        return [_resource(row) for row in team_rows if row.published_version_id]
    if name == "atlas_list_workflows":
        _require_scope(context, "mcp:read")
        workflow_rows = await WorkflowRepository(session, context).list_configs()
        return [_resource(row) for row in workflow_rows if row.published_version_id]
    if name == "atlas_list_sessions":
        _require_scope(context, "mcp:sessions:read")
        session_rows = await SessionRepository(session, context).list_for_user(
            include_all_users=context.can_administer()
        )
        return [_session(row) for row in session_rows]
    if name in {"atlas_run_agent", "atlas_run_team", "atlas_run_workflow"}:
        _require_scope(context, "mcp:run")
        return await _run(name, arguments, session, context)
    raise LookupError(f"Unknown MCP tool: {name}")


def _rpc_result(request_id: str | int | None, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


@router.post("/mcp", response_model=None)
async def mcp_gateway(
    payload: JsonRpcRequest,
    context: Context,
    session: TenantSession,
) -> dict[str, Any] | Response:
    # Middleware has already enforced mcp:access for service accounts.
    if context.principal_type != "service_account":
        _require_scope(context, "mcp:access")
    settings = await _get_settings(session, context)
    if settings is None or not settings.enabled:
        raise HTTPException(status_code=403, detail="MCP is disabled for this tenant")

    if payload.method == "initialize":
        return _rpc_result(
            payload.id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "Atlas tenant MCP gateway", "version": "1.0"},
            },
        )
    if payload.method == "notifications/initialized":
        return Response(status_code=204)
    if payload.method == "ping":
        return _rpc_result(payload.id, {})
    if payload.method == "tools/list":
        return _rpc_result(payload.id, {"tools": _tools()})
    if payload.method == "tools/call":
        name = str(payload.params.get("name", ""))
        arguments = payload.params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return {
                "jsonrpc": "2.0",
                "id": payload.id,
                "error": {"code": -32602, "message": "arguments must be an object"},
            }
        try:
            result = await _call_tool(name, arguments, session, context)
            return _rpc_result(
                payload.id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, default=str),
                        }
                    ]
                },
            )
        except (LookupError, ValueError) as exc:
            return _rpc_result(
                payload.id,
                {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            )
    return {
        "jsonrpc": "2.0",
        "id": payload.id,
        "error": {"code": -32601, "message": "Method not found"},
    }
