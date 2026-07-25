from types import SimpleNamespace

import pytest

from app.tools.custom import CUSTOM_TOOL_BY_KEY
from app.tools.providers import (
    PROVIDERS,
    CustomPythonProvider,
    MCPProvider,
    OpenAPIProvider,
    ProviderBuildContext,
    ProviderValidationError,
    PythonToolkitProvider,
    toolkit_catalog,
    validate_provider_config,
)
from app.tools.registry import SafeRestClient
from app.tools.toolkit_catalog import BLOCKED_MODULES, TOOLKIT_BY_KEY


def openapi_config(**overrides):
    document = {
        "openapi": "3.0.3",
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/widgets/{widget_id}": {
                "get": {
                    "operationId": "getWidget",
                    "parameters": [
                        {
                            "name": "widget_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                },
                "delete": {
                    "operationId": "deleteWidget",
                    "parameters": [
                        {
                            "name": "widget_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                },
            }
        },
    }
    return {
        "document": document,
        "allowed_operations": ["getWidget"],
    } | overrides


def test_python_registry_rejects_module_paths_and_unknown_options():
    with pytest.raises(ProviderValidationError):
        validate_provider_config(
            "python_toolkit",
            {"toolkit": "tenant.module.Toolkit", "options": {}},
        )
    with pytest.raises(ProviderValidationError):
        validate_provider_config(
            "python_toolkit",
            {"toolkit": "calculator", "options": {"command": "rm -rf /"}},
        )


def test_custom_python_registry_rejects_database_controlled_imports():
    with pytest.raises(ProviderValidationError, match="source-controlled registry"):
        validate_provider_config(
            "custom_python",
            {
                "custom_tool": "tenant.module.ArbitraryTool",
                "settings": {},
            },
        )
    with pytest.raises(ProviderValidationError, match="Unsupported settings"):
        validate_provider_config(
            "custom_python",
            {
                "custom_tool": "signed_rest_api",
                "settings": {
                    "base_url": "https://api.example.com",
                    "python_code": "import os",
                },
            },
        )
    assert set(CUSTOM_TOOL_BY_KEY) == {"signed_rest_api"}


@pytest.mark.asyncio
async def test_custom_python_uses_safe_client_and_marks_mutations(monkeypatch):
    client = SafeRestClient({"api.example.com"})
    checked_urls = []

    async def safe(url):
        checked_urls.append(url)

    monkeypatch.setattr(client, "validate_url", safe)
    config = {
        "custom_tool": "signed_rest_api",
        "settings": {"base_url": "https://api.example.com"},
        "include_tools": ["get_resource", "create_resource"],
    }
    provider = CustomPythonProvider()
    capabilities = await provider.enumerate_tools(config, client, {})
    assert checked_urls == ["https://api.example.com"]
    assert [item.name for item in capabilities] == [
        "get_resource",
        "create_resource",
    ]
    assert capabilities[0].approval_required is False
    assert capabilities[1].approval_required is True

    tools = await provider.build_tools(
        config,
        ProviderBuildContext(
            client=client,
            prefix="crm",
            headers={},
            approval_required=False,
            credential_provider="rest_api",
            credential_value="tenant-secret",
        ),
    )
    assert [tool.__name__ for tool in tools] == [
        "get_resource",
        "create_resource",
    ]
    assert getattr(tools[1], "requires_confirmation", True)


@pytest.mark.asyncio
async def test_custom_python_requires_matching_credential_provider(monkeypatch):
    client = SafeRestClient({"api.example.com"})

    async def safe(_url):
        return None

    monkeypatch.setattr(client, "validate_url", safe)
    with pytest.raises(ProviderValidationError, match="provider 'rest_api'"):
        await CustomPythonProvider().build_tools(
            {
                "custom_tool": "signed_rest_api",
                "settings": {"base_url": "https://api.example.com"},
            },
            ProviderBuildContext(
                client=client,
                prefix="crm",
                headers={},
                approval_required=False,
                credential_provider="openai",
                credential_value="wrong-provider",
            ),
        )


def test_provider_redaction_is_defensive():
    redacted = PROVIDERS["http"].redact_config(
        {"base_url": "https://api.example.com", "api_token": "must-not-leak"}
    )
    assert redacted["api_token"].startswith("[red")


@pytest.mark.asyncio
async def test_openapi_enumeration_and_selected_operation_filter(monkeypatch):
    client = SafeRestClient({"api.example.com"})

    async def safe(_url):
        return None

    monkeypatch.setattr(client, "validate_url", safe)
    provider = OpenAPIProvider()
    capabilities = await provider.enumerate_tools(openapi_config(), client, {})
    assert [item.name for item in capabilities] == ["getWidget", "deleteWidget"]
    assert capabilities[0].approval_required is False
    assert capabilities[1].approval_required is True

    tools = await provider.build_tools(
        openapi_config(),
        ProviderBuildContext(
            client=client,
            prefix="inventory",
            headers={},
            approval_required=False,
        ),
    )
    assert [tool.__name__ for tool in tools] == ["getWidget"]


@pytest.mark.asyncio
async def test_openapi_mutation_forces_confirmation(monkeypatch):
    client = SafeRestClient({"api.example.com"})

    async def safe(_url):
        return None

    monkeypatch.setattr(client, "validate_url", safe)
    tools = await OpenAPIProvider().build_tools(
        openapi_config(allowed_operations=["deleteWidget"]),
        ProviderBuildContext(
            client=client,
            prefix="inventory",
            headers={},
            approval_required=False,
        ),
    )
    assert getattr(tools[0], "requires_confirmation", True)


@pytest.mark.asyncio
async def test_python_toolkit_functions_keep_capability_names():
    tools = await PythonToolkitProvider().build_tools(
        {"toolkit": "calculator", "options": {}, "include_tools": ["add"]},
        ProviderBuildContext(
            client=SafeRestClient(set()),
            prefix="finance",
            headers={},
            approval_required=False,
        ),
    )
    assert list(tools[0].functions) == ["add"]


def test_toolkit_catalog_never_exposes_blocked_modules():
    catalog = toolkit_catalog()
    exposed = {item["module"] for item in catalog if item["exposed"]}
    assert not exposed & BLOCKED_MODULES
    assert all(
        item["available"] or item["unavailable_reason"]
        for item in catalog
        if item["tier"] != "blocked"
    )
    assert len(TOOLKIT_BY_KEY) == len(catalog)


def test_smtp_email_toolkit_is_available_with_sender_options():
    spec = TOOLKIT_BY_KEY["email"]
    assert spec.disabled_reason is None
    assert set(spec.options) == {"sender_email", "sender_name", "receiver_email"}
    email = next(item for item in toolkit_catalog() if item["key"] == "email")
    assert email["available"] is True
    assert email["status"] == "needs_credential"
    assert email["credentials"][0]["kwarg"] == "sender_passkey"


def test_multi_value_toolkits_expose_options_instead_of_false_package_errors():
    whatsapp = TOOLKIT_BY_KEY["whatsapp"]
    assert whatsapp.disabled_reason is None
    assert "phone_number_id" in whatsapp.options

    zoom = TOOLKIT_BY_KEY["zoom"]
    assert zoom.credentials[0].kwarg == "client_secret"
    assert set(zoom.options) >= {"account_id", "client_id"}

    reddit = TOOLKIT_BY_KEY["reddit"]
    assert reddit.disabled_reason is None
    assert "client_id" in reddit.options

    catalog = {item["key"]: item for item in toolkit_catalog()}
    assert catalog["aws_ses"]["available"] is False
    assert "host AWS credential chain" in (catalog["aws_ses"]["unavailable_reason"] or "")
    assert catalog["aws_ses"]["status"] == "blocked"
    assert catalog["crawl4ai"]["status"] == "blocked"
    assert catalog["gmail"]["status"] == "blocked"
    assert catalog["youcom"]["class_name"] == "YouTools"
    assert catalog["google_maps"]["class_name"] == "GoogleMapTools"
    assert catalog["apify"]["credentials"][0]["kwarg"] == "apify_api_token"


@pytest.mark.asyncio
async def test_credential_toolkit_requires_matching_server_side_credential():
    context = ProviderBuildContext(
        client=SafeRestClient(set()),
        prefix="media",
        headers={},
        approval_required=False,
    )
    with pytest.raises(ProviderValidationError, match="openai tenant credential"):
        await PythonToolkitProvider().build_tools(
            {"toolkit": "dalle", "options": {}},
            context,
        )

    context.credential_provider = "rest_api"
    context.credential_value = "not-a-real-secret"
    with pytest.raises(ProviderValidationError, match="provider 'openai'"):
        await PythonToolkitProvider().build_tools(
            {"toolkit": "dalle", "options": {}},
            context,
        )


@pytest.mark.asyncio
async def test_side_effect_toolkit_marks_mutations_for_approval():
    context = ProviderBuildContext(
        client=SafeRestClient(set()),
        prefix="media",
        headers={},
        approval_required=False,
        credential_provider="openai",
        credential_value="not-a-real-secret",
    )
    toolkit = (
        await PythonToolkitProvider().build_tools(
            {"toolkit": "dalle", "options": {}},
            context,
        )
    )[0]
    create_image = next(iter(toolkit.functions.values()))
    assert create_image.requires_confirmation is True


@pytest.mark.asyncio
async def test_mcp_discovery_closes_mock_client(monkeypatch):
    events = []

    class FakeToolkit:
        include_tools = ["selected"]
        functions = {
            "preview_read": SimpleNamespace(
                name="preview_read",
                description="Read data",
                parameters={"type": "object", "properties": {}},
            )
        }

        async def connect(self):
            events.append("connect")

        async def close(self):
            events.append("close")

    provider = MCPProvider()
    monkeypatch.setattr(provider, "_toolkit", lambda *_args: FakeToolkit())
    client = SafeRestClient({"mcp.example.com"})

    async def safe(_url):
        return None

    monkeypatch.setattr(client, "validate_url", safe)
    capabilities = await provider.enumerate_tools(
        {
            "transport": "streamable-http",
            "url": "https://mcp.example.com/mcp",
            "include_tools": ["selected"],
        },
        client,
        {},
    )
    assert [item.name for item in capabilities] == ["read"]
    assert events == ["connect", "close"]
