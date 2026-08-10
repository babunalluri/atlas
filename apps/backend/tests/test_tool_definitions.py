import inspect
import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.agent_runtime.factory import AgentFactoryService
from app.api.schemas import ToolDefinitionCreateIn
from app.api.tools import _out, _validate_python_toolkit_credential
from app.db.models import AgentToolBinding
from app.db.repositories import (
    AgentRepository,
    CredentialRepository,
    ToolDefinitionRepository,
    ToolDefinitionVersionRepository,
)


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
async def test_sync_tenant_python_draft_discovers_empty_capabilities(session, tenant_a):
    """Saving with capabilities=[] should AST-discover and persist method names."""
    from app.api.tools import _sync_tenant_python_draft

    session.info["tenant_id"] = tenant_a.tenant_id
    tools = ToolDefinitionRepository(session, tenant_a)
    versions = ToolDefinitionVersionRepository(session, tenant_a)
    source = (
        "async def get_ticket(ctx, ticket_id: int):\n"
        "    return ticket_id\n"
        "async def search_tickets(ctx, query: str = ''):\n"
        "    return query\n"
    )
    tool = await tools.create(
        tool_values(
            kind="tenant_python",
            http_method=None,
            base_url=None,
            path=None,
            slug="freshdesk-caps",
            name="Freshdesk Caps",
            config={
                "source_code": source,
                "dependencies": [],
                "capabilities": [],
                "settings": {},
                "version_status": "draft",
            },
        )
    )
    await _sync_tenant_python_draft(tool, tenant_a, session)
    await session.refresh(tool)
    names = {item["name"] for item in tool.config.get("capabilities") or []}
    assert names == {"get_ticket", "search_tickets"}
    draft = await versions.latest_draft(tool.id)
    assert draft is not None
    assert {item["name"] for item in draft.capabilities} == names


@pytest.mark.asyncio
async def test_tool_update_then_out_avoids_missing_greenlet(session, tenant_a):
    """Regression: flush+onupdate expires updated_at; sync _out must not lazy-load."""
    from app.api.tools import _serialize_tool, _sync_tenant_python_draft

    session.info["tenant_id"] = tenant_a.tenant_id
    created = await ToolDefinitionRepository(session, tenant_a).create(
        tool_values(
            kind="tenant_python",
            http_method=None,
            base_url=None,
            path=None,
            slug="freshdesk-like",
            name="Freshdesk like",
            config={
                "source_code": "async def list_tickets(ctx):\n    return []\n" + ("# x\n" * 5000),
                "dependencies": [],
                "capabilities": [{"name": "list_tickets", "mutating": False}],
                "settings": {"base_url": "https://example.freshdesk.com"},
                "version_status": "draft",
            },
        )
    )
    updated = await ToolDefinitionRepository(session, tenant_a).update(
        created.id,
        {
            "config": {
                **created.config,
                "source_code": created.config["source_code"] + "\n# edited\n",
            }
        },
    )
    assert updated is not None
    await _sync_tenant_python_draft(updated, tenant_a, session)
    out = await _serialize_tool(session, updated)
    assert out.id == created.id
    assert out.updated_at is not None


def _tenant_python_values(slug: str, source: str, **overrides):
    return tool_values(
        kind="tenant_python",
        http_method=None,
        base_url=None,
        path=None,
        slug=slug,
        name=slug.replace("-", " ").title(),
        config={
            "source_code": source,
            "dependencies": [],
            "capabilities": [{"name": "list_tickets", "mutating": False}],
            "settings": {"base_url": "https://example.freshdesk.com"},
            "version_status": "draft",
        },
        **overrides,
    )


@pytest.mark.asyncio
async def test_tenant_python_list_versions_and_restore(session, tenant_a):
    """Publish two snapshots; restore live pointer and clone as draft."""
    session.info["tenant_id"] = tenant_a.tenant_id
    tools = ToolDefinitionRepository(session, tenant_a)
    versions = ToolDefinitionVersionRepository(session, tenant_a)

    v1_source = "async def list_tickets(ctx):\n    return {'v': 1}\n"
    v2_source = "async def list_tickets(ctx):\n    return {'v': 2}\n"
    tool = await tools.create(_tenant_python_values("versioned-python", v1_source))

    draft1 = await versions.upsert_draft(
        tool_definition_id=tool.id,
        source_code=v1_source,
        dependencies=[],
        capabilities=[{"name": "list_tickets", "mutating": False}],
        settings={"base_url": "https://example.freshdesk.com"},
        created_by=tenant_a.user_id,
    )
    published1 = await versions.publish(draft1.id, tool)
    assert published1 is not None
    assert tool.published_version_id == published1.id
    assert tool.config["source_code"] == v1_source
    assert tool.config["version_status"] == "published"

    draft2 = await versions.upsert_draft(
        tool_definition_id=tool.id,
        source_code=v2_source,
        dependencies=[],
        capabilities=[{"name": "list_tickets", "mutating": False}],
        settings={"base_url": "https://example.freshdesk.com"},
        created_by=tenant_a.user_id,
    )
    published2 = await versions.publish(draft2.id, tool)
    assert published2 is not None
    assert tool.published_version_id == published2.id
    assert tool.config["source_code"] == v2_source

    listed = list(await versions.list_for_tool(tool.id))
    assert [row.version for row in listed] == [2, 1]

    restored = await versions.restore_version(
        tool, published1.id, as_draft=False, created_by=tenant_a.user_id
    )
    assert restored.id == published1.id
    assert tool.published_version_id == published1.id
    assert tool.config["source_code"] == v1_source
    assert tool.config["version_status"] == "published"

    draft = await versions.restore_version(
        tool, published2.id, as_draft=True, created_by=tenant_a.user_id
    )
    assert draft.status == "draft"
    assert draft.version == 3
    assert draft.source_code == v2_source
    assert tool.published_version_id == published1.id
    assert tool.config["version_status"] == "draft"
    assert tool.config["source_code"] == v2_source


@pytest.mark.asyncio
async def test_tenant_python_restore_version_tenant_isolation(session, tenant_a, tenant_b):
    session.info["tenant_id"] = tenant_a.tenant_id
    tools_a = ToolDefinitionRepository(session, tenant_a)
    versions_a = ToolDefinitionVersionRepository(session, tenant_a)
    tool = await tools_a.create(
        _tenant_python_values(
            "isolated-python",
            "async def list_tickets(ctx):\n    return []\n",
        )
    )
    draft = await versions_a.upsert_draft(
        tool_definition_id=tool.id,
        source_code=tool.config["source_code"],
        dependencies=[],
        capabilities=[{"name": "list_tickets", "mutating": False}],
        settings={"base_url": "https://example.freshdesk.com"},
        created_by=tenant_a.user_id,
    )
    published = await versions_a.publish(draft.id, tool)
    assert published is not None
    await session.commit()

    session.info["tenant_id"] = tenant_b.tenant_id
    versions_b = ToolDefinitionVersionRepository(session, tenant_b)
    assert list(await versions_b.list_for_tool(tool.id)) == []
    assert await versions_b.get(published.id) is None


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


@pytest.mark.asyncio
async def test_credential_delete_removes_unused_secret(session, tenant_a):
    session.info["tenant_id"] = tenant_a.tenant_id
    repo = CredentialRepository(session, tenant_a)
    credential = await repo.create(
        name="Old Groq",
        provider="groq",
        encrypted_value="ciphertext",
        key_version="local-v1",
    )
    await session.commit()

    assert await repo.delete(credential.id) is True
    assert await repo.get(credential.id) is None


@pytest.mark.asyncio
async def test_credential_delete_blocked_when_attached_to_tool(session, tenant_a):
    session.info["tenant_id"] = tenant_a.tenant_id
    credentials = CredentialRepository(session, tenant_a)
    credential = await credentials.create(
        name="API token",
        provider="rest_api",
        encrypted_value="ciphertext",
        key_version="local-v1",
    )
    await ToolDefinitionRepository(session, tenant_a).create(
        tool_values(credential_id=credential.id)
    )
    await session.commit()

    with pytest.raises(ValueError, match="attached to a tool"):
        await credentials.delete(credential.id)
    assert await credentials.get(credential.id) is not None
