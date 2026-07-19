import pytest

from app.tools.registry import SafeRestClient, UnsafeOutboundRequest


@pytest.mark.asyncio
async def test_rest_client_blocks_private_hosts():
    client = SafeRestClient({"127.0.0.1", "localhost"})
    with pytest.raises(UnsafeOutboundRequest):
        await client.validate_url("https://127.0.0.1/secret")


@pytest.mark.asyncio
async def test_rest_client_requires_allowlist():
    client = SafeRestClient({"api.example.com"})
    with pytest.raises(UnsafeOutboundRequest):
        await client.validate_url("https://evil.example.net/x")


@pytest.mark.asyncio
async def test_rest_client_rejects_http():
    client = SafeRestClient({"api.example.com"})
    with pytest.raises(UnsafeOutboundRequest):
        await client.validate_url("http://api.example.com/x")
