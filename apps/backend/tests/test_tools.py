import pytest
from fastapi import HTTPException

from app.api.tools import _validate_targets
from app.core.settings import (
    DEFAULT_ALLOWED_OUTBOUND_HOSTS,
    GROWW_API_HOST,
    GROWW_MCP_HOST,
    Settings,
)
from app.tools.registry import SafeRestClient, UnsafeOutboundRequest


@pytest.mark.asyncio
async def test_rest_client_blocks_private_hosts():
    client = SafeRestClient({"127.0.0.1", "localhost"})
    with pytest.raises(UnsafeOutboundRequest):
        await client.validate_url("https://127.0.0.1/secret")


@pytest.mark.asyncio
async def test_rest_client_requires_allowlist():
    client = SafeRestClient({"api.example.com"})
    with pytest.raises(UnsafeOutboundRequest, match="evil.example.net"):
        await client.validate_url("https://evil.example.net/x")


@pytest.mark.asyncio
async def test_rest_client_rejects_http():
    client = SafeRestClient({"api.example.com"})
    with pytest.raises(UnsafeOutboundRequest, match="api.example.com"):
        await client.validate_url("http://api.example.com/x")


def test_default_outbound_hosts_include_groww_mcp():
    assert GROWW_MCP_HOST in DEFAULT_ALLOWED_OUTBOUND_HOSTS
    assert GROWW_API_HOST in DEFAULT_ALLOWED_OUTBOUND_HOSTS
    assert "*" not in DEFAULT_ALLOWED_OUTBOUND_HOSTS


def test_development_settings_keep_groww_mcp_without_ops_env():
    settings = Settings(
        environment="development",
        allowed_outbound_hosts={"api.example.com"},
    )
    assert GROWW_MCP_HOST in settings.allowed_outbound_hosts
    assert GROWW_API_HOST in settings.allowed_outbound_hosts
    assert "api.example.com" in settings.allowed_outbound_hosts


@pytest.mark.asyncio
async def test_admin_tools_accept_groww_mcp_and_reject_unknown_hosts(monkeypatch):
    async def fake_resolve(host: str, port: int) -> set[str]:
        assert host == GROWW_MCP_HOST
        return {"1.1.1.1"}

    monkeypatch.setattr("app.tools.registry._resolve", fake_resolve)
    settings = Settings(allowed_outbound_hosts=set(DEFAULT_ALLOWED_OUTBOUND_HOSTS))

    await _validate_targets("mcp", {"url": "https://mcp.groww.in/mcp"}, settings)

    with pytest.raises(HTTPException) as http_error:
        await _validate_targets("mcp", {"url": "http://mcp.groww.in/mcp"}, settings)
    assert http_error.value.status_code == 422
    assert "mcp.groww.in" in str(http_error.value.detail)

    with pytest.raises(HTTPException) as http_error:
        await _validate_targets("mcp", {"url": "http://evil.example/mcp"}, settings)
    assert http_error.value.status_code == 422
    detail = str(http_error.value.detail)
    assert "evil.example" in detail
    assert "allowlisted" in detail

    with pytest.raises(HTTPException) as http_error:
        await _validate_targets("mcp", {"url": "https://evil.example/mcp"}, settings)
    assert http_error.value.status_code == 422
    detail = str(http_error.value.detail)
    assert "evil.example" in detail
    assert "allowlisted" in detail
    assert "REST_TOOL_ALLOWED_HOSTS" in detail
