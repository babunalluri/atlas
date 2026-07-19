import uuid

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.api.mcp import JsonRpcRequest, _call_tool, _get_settings, _tools, mcp_gateway
from app.auth.middleware import TenantAuthMiddleware
from app.db.models import Role, TenantMcpSettings
from app.db.repositories import AgentRepository
from app.tenancy.context import TenantContext


@pytest.mark.asyncio
async def test_mcp_rejects_missing_auth(client):
    response = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_service_account_without_mcp_access_cannot_reach_route(monkeypatch):
    reached = False
    app = FastAPI()

    @app.post("/mcp")
    async def route():
        nonlocal reached
        reached = True
        return {}

    context = TenantContext(
        tenant_id=uuid.uuid4(),
        user_id="sa:test",
        role=Role.end_user,
        clerk_org_id="org",
        scopes=("mcp:run",),
        principal_type="service_account",
    )

    async def fake_require(*args, **kwargs):
        return context

    monkeypatch.setattr("app.auth.middleware.require_tenant", fake_require)
    app.add_middleware(TenantAuthMiddleware)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.post("/mcp", headers={"Authorization": "Bearer token"})
    assert response.status_code == 403
    assert reached is False


@pytest.mark.asyncio
async def test_invalid_auth_never_reaches_mcp_route(monkeypatch):
    reached = False
    app = FastAPI()

    @app.post("/mcp")
    async def route():
        nonlocal reached
        reached = True
        return {}

    async def reject(*args, **kwargs):
        raise HTTPException(status_code=401, detail="Invalid token")

    monkeypatch.setattr("app.auth.middleware.require_tenant", reject)
    app.add_middleware(TenantAuthMiddleware)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.post("/mcp", headers={"Authorization": "Bearer invalid"})
    assert response.status_code == 401
    assert reached is False


@pytest.mark.asyncio
async def test_mcp_resources_and_settings_are_tenant_isolated(
    session, tenant_a, tenant_b
):
    tenant_a = TenantContext(
        **{
            "tenant_id": tenant_a.tenant_id,
            "user_id": tenant_a.user_id,
            "role": Role.end_user,
            "clerk_org_id": tenant_a.clerk_org_id,
            "scopes": ("mcp:access", "mcp:read"),
            "principal_type": "service_account",
        }
    )
    tenant_b = TenantContext(
        **{
            "tenant_id": tenant_b.tenant_id,
            "user_id": tenant_b.user_id,
            "role": Role.end_user,
            "clerk_org_id": tenant_b.clerk_org_id,
            "scopes": ("mcp:access", "mcp:read", "mcp:run"),
            "principal_type": "service_account",
        }
    )
    session.info["tenant_id"] = tenant_a.tenant_id
    agents = AgentRepository(session, tenant_a)
    agent = await agents.create_config(slug="alpha", name="Alpha")
    version = await agents.create_draft(
        config_id=agent.id,
        instructions="Return a short answer.",
        model_id="openai:gpt-4.1-mini",
        temperature=0,
    )
    await agents.publish(version.id)
    session.add(
        TenantMcpSettings(
            id=uuid.uuid4(),
            tenant_id=tenant_a.tenant_id,
            enabled=True,
            updated_by=tenant_a.user_id,
        )
    )
    await session.flush()

    visible_a = await _call_tool("atlas_list_agents", {}, session, tenant_a)
    session.info["tenant_id"] = tenant_b.tenant_id
    visible_b = await _call_tool("atlas_list_agents", {}, session, tenant_b)
    assert [row["slug"] for row in visible_a] == ["alpha"]
    assert visible_b == []
    with pytest.raises(LookupError, match="Published agent not found"):
        await _call_tool(
            "atlas_run_agent",
            {"config_id": str(agent.id), "message": "Cross-tenant attempt"},
            session,
            tenant_b,
        )
    session.info["tenant_id"] = tenant_a.tenant_id
    assert (await _get_settings(session, tenant_a)).enabled is True
    session.info["tenant_id"] = tenant_b.tenant_id
    assert await _get_settings(session, tenant_b) is None


@pytest.mark.asyncio
async def test_disabled_tenant_cannot_initialize_mcp(session, tenant_a):
    with pytest.raises(HTTPException) as error:
        await mcp_gateway(
            JsonRpcRequest(id=1, method="initialize"),
            tenant_a,
            session,
        )
    assert error.value.status_code == 403


def test_mcp_tool_inputs_never_accept_tenant_id():
    schemas = [tool["inputSchema"] for tool in _tools()]
    assert all("tenant_id" not in schema.get("properties", {}) for schema in schemas)
