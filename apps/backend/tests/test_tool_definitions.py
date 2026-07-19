import inspect
import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.agent_runtime.factory import AgentFactoryService
from app.api.schemas import ToolDefinitionCreateIn
from app.api.tools import _out, _validate_python_toolkit_credential
from app.db.models import AgentToolBinding
from app.db.repositories import AgentRepository, CredentialRepository, ToolDefinitionRepository


def tool_values(**overrides):
    values = {
        "name": "Customer lookup",
        "slug": "customer-lookup",
        "description": "Look up one customer",
        "kind": "http",
        "http_method": "GET",
        "base_url": "https://api.example.com",
        "path": "/customers/{customer_id}",
        "request_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
        "response_description": None,
        "response_schema": None,
        "headers": {},
        "config": {
            "base_url": "https://api.example.com",
            "method": "GET",
            "path": "/customers/{customer_id}",
            "request_schema": {
                "type": "object",
                "properties": {"customer_id": {"type": "string"}},
                "required": ["customer_id"],
            },
            "headers": {},
            "credential_header": "Authorization",
            "credential_prefix": "Bearer ",
            "timeout_seconds": 10,
        },
        "credential_id": None,
        "approval_required": False,
        "active": True,
        "connection_status": "unvalidated",
    }
    return values | overrides


@pytest.mark.asyncio
async def test_tool_repository_is_tenant_scoped(session, tenant_a, tenant_b):
    session.info["tenant_id"] = tenant_a.tenant_id
    created = await ToolDefinitionRepository(session, tenant_a).create(tool_values())
    await session.commit()

    session.info["tenant_id"] = tenant_b.tenant_id
    assert await ToolDefinitionRepository(session, tenant_b).get(created.id) is None
    assert list(await ToolDefinitionRepository(session, tenant_b).list()) == []


@pytest.mark.asyncio
async def test_tool_rejects_cross_tenant_credential(session, tenant_a, tenant_b):
    session.info["tenant_id"] = tenant_b.tenant_id
    credential = await CredentialRepository(session, tenant_b).create(
        name="Other tenant secret",
        provider="rest_api",
        encrypted_value="encrypted-not-plaintext",
        key_version="local-v1",
    )
    await session.commit()

    session.info["tenant_id"] = tenant_a.tenant_id
    with pytest.raises(LookupError, match="Credential not found"):
        await ToolDefinitionRepository(session, tenant_a).create(
            tool_values(credential_id=credential.id)
        )


@pytest.mark.asyncio
async def test_credential_toolkit_creation_is_tenant_and_provider_gated(
    session, tenant_a, tenant_b
):
    session.info["tenant_id"] = tenant_a.tenant_id
    with pytest.raises(HTTPException, match="requires a 'openai' credential"):
        await _validate_python_toolkit_credential(
            "python_toolkit",
            {"toolkit": "dalle"},
            None,
            tenant_a,
            session,
        )

    session.info["tenant_id"] = tenant_b.tenant_id
    other_tenant = await CredentialRepository(session, tenant_b).create(
        name="Other OpenAI",
        provider="openai",
        encrypted_value="ciphertext",
        key_version="local-v1",
    )
    await session.commit()
    session.info["tenant_id"] = tenant_a.tenant_id
    with pytest.raises(HTTPException, match="Credential not found for tenant"):
        await _validate_python_toolkit_credential(
            "python_toolkit",
            {"toolkit": "dalle"},
            other_tenant.id,
            tenant_a,
            session,
        )


@pytest.mark.asyncio
async def test_tool_api_output_never_exposes_credential_value(session, tenant_a):
    session.info["tenant_id"] = tenant_a.tenant_id
    credential = await CredentialRepository(session, tenant_a).create(
        name="API token",
        provider="rest_api",
        encrypted_value="ciphertext-must-not-leak",
        key_version="local-v1",
    )
    definition = await ToolDefinitionRepository(session, tenant_a).create(
        tool_values(credential_id=credential.id)
    )

    output = _out(definition).model_dump(mode="json")
    assert output["credential_id"] == str(credential.id)
    assert "ciphertext-must-not-leak" not in str(output)
    assert "encrypted_value" not in output


def test_tool_schema_rejects_plaintext_secret_headers_and_private_url():
    with pytest.raises(ValidationError, match="TenantCredential"):
        ToolDefinitionCreateIn.model_validate(
            tool_values(headers={"Authorization": "Bearer plaintext"})
        )
    with pytest.raises(ValidationError, match="HTTPS"):
        ToolDefinitionCreateIn.model_validate(tool_values(base_url="http://127.0.0.1/internal"))


@pytest.mark.asyncio
async def test_runtime_resolves_tenant_definition(session, tenant_a):
    session.info["tenant_id"] = tenant_a.tenant_id
    definition = await ToolDefinitionRepository(session, tenant_a).create(tool_values())
    agent = await AgentRepository(session, tenant_a).create_config(slug="agent", name="Agent")
    version = await AgentRepository(session, tenant_a).create_draft(
        config_id=agent.id,
        instructions="Use tools",
        model_id="openai:gpt-4.1-mini",
        temperature=0.2,
        tools=[{"tool_definition_id": definition.id, "config": {}}],
    )
    binding = (await AgentRepository(session, tenant_a).bindings(version.id))[0]

    callable_tool = await AgentFactoryService(
        session, tenant_a, allowed_hosts={"api.example.com"}
    )._build_tool(binding)
    assert callable_tool.__name__ == "customer_lookup"
    assert "customer_id" in inspect.signature(callable_tool).parameters


@pytest.mark.asyncio
async def test_runtime_never_resolves_foreign_definition(session, tenant_a, tenant_b):
    session.info["tenant_id"] = tenant_b.tenant_id
    foreign = await ToolDefinitionRepository(session, tenant_b).create(tool_values())
    await session.commit()

    session.info["tenant_id"] = tenant_a.tenant_id
    binding = AgentToolBinding(
        id=uuid.uuid4(),
        tenant_id=tenant_a.tenant_id,
        agent_version_id=uuid.uuid4(),
        tool_definition_id=foreign.id,
        config={},
    )
    with pytest.raises(ValueError, match="not found for tenant"):
        await AgentFactoryService(session, tenant_a, allowed_hosts={"api.example.com"})._build_tool(
            binding
        )
