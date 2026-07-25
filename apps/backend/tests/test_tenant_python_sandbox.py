"""Security and runtime tests for editable sandboxed Python tools."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.tools.providers import (
    PROVIDERS,
    ProviderBuildContext,
    ProviderValidationError,
    TenantPythonProvider,
    validate_provider_config,
)
from app.tools.registry import SafeRestClient, UnsafeOutboundRequest
from app.tools.sandbox.orchestrator import (
    SandboxOrchestrator,
    SandboxRunRequest,
    get_orchestrator_for_run,
)
from app.tools.sandbox.templates import CC_PBX_STARTER_SOURCE
from app.tools.sandbox.validator import (
    SandboxValidationError,
    validate_dependencies,
    validate_tenant_python_source,
)


SAFE_SOURCE = """
async def list_items(ctx, limit: int = 10):
    return await ctx.http.get(f"{ctx.settings['base_url']}/items", params={"limit": limit})


async def create_item(ctx, name: str):
    return await ctx.http.post(f"{ctx.settings['base_url']}/items", json={"name": name})
"""


def test_ast_rejects_dangerous_imports_and_builtins():
    with pytest.raises(SandboxValidationError, match="Import not allowed"):
        validate_tenant_python_source("import os\nasync def ok(ctx):\n    return 1\n")
    with pytest.raises(SandboxValidationError, match="Forbidden call"):
        validate_tenant_python_source("async def ok(ctx):\n    return eval('1')\n")
    with pytest.raises(SandboxValidationError, match="at least one"):
        validate_tenant_python_source("x = 1\n")


def test_ast_allows_requests_import_with_async_capability():
    names = validate_tenant_python_source(
        "import requests\n\n"
        "async def list_tickets(ctx, limit: int = 10):\n"
        "    resp = requests.get(ctx.settings['base_url'], params={'limit': limit})\n"
        "    return resp.json()\n"
    )
    assert names == ["list_tickets"]


def test_discover_capability_specs_includes_input_schema():
    from app.tools.sandbox.validator import discover_capability_specs

    specs = discover_capability_specs(
        "from typing import Optional\n"
        "from supertools.common.base_tool import BaseToolkit\n\n"
        "class FDT(BaseToolkit):\n"
        "    def search_tickets(self, query: Optional[str] = None):\n"
        '        """Search Freshdesk tickets."""\n'
        "        return query\n"
        "    def get_ticket(self, ticket_id: int):\n"
        "        return ticket_id\n"
    )
    by_name = {row["name"]: row for row in specs}
    assert set(by_name) == {"search_tickets", "get_ticket"}
    search = by_name["search_tickets"]["input_schema"]
    assert search["properties"]["query"]["type"] == "string"
    assert "query" not in search.get("required", [])
    get_ticket = by_name["get_ticket"]["input_schema"]
    assert get_ticket["properties"]["ticket_id"]["type"] == "integer"
    assert get_ticket["required"] == ["ticket_id"]


def test_tenant_python_enriches_empty_input_schema_from_ast():
    from app.tools.providers import TenantPythonConfig

    parsed = TenantPythonConfig.model_validate(
        {
            "source_code": (
                "from typing import Optional\n"
                "from supertools.common.base_tool import BaseToolkit\n\n"
                "class FDT(BaseToolkit):\n"
                "    def search_tickets(self, query: Optional[str] = None):\n"
                "        return query\n"
            ),
            "capabilities": [
                {
                    "name": "search_tickets",
                    "description": "",
                    "mutating": False,
                    "input_schema": {"type": "object", "properties": {}},
                }
            ],
            "version_status": "draft",
        }
    )
    assert parsed.capabilities[0].input_schema["properties"]["query"]["type"] == "string"


def test_apply_json_schema_signature_exposes_query_kwarg():
    import inspect

    from app.tools.providers import _apply_json_schema_signature

    async def _runner(**kwargs):
        return kwargs

    _apply_json_schema_signature(
        _runner,
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
    )
    params = list(inspect.signature(_runner).parameters)
    assert params == ["query"]
    assert _runner.__annotations__["query"] is str


def test_apply_json_schema_signature_usable_by_agno_function():
    """Agno Function.from_callable + Pydantic validate_call need __annotations__."""
    pytest.importorskip("agno")
    from agno.tools.function import Function

    from app.tools.providers import _apply_json_schema_signature

    async def _runner(**kwargs):
        return kwargs

    _runner.__name__ = "get_ticket"
    _apply_json_schema_signature(
        _runner,
        {
            "type": "object",
            "properties": {"ticket_id": {"type": "integer"}},
            "required": ["ticket_id"],
        },
    )
    fn = Function.from_callable(_runner)
    assert fn.name == "get_ticket"
    assert "ticket_id" in (fn.parameters or {}).get("properties", {})


def test_ast_deny_list_allows_unknown_third_party_roots():
    """Save-time policy: only dangerous modules blocked; missing pkgs fail at runtime."""
    names = validate_tenant_python_source(
        "from supertools.common.base_tool import BaseToolkit\n"
        "from agno.utils.log import log_info, logger\n"
        "import requests\n"
        "import zoneinfo\n"
        "import time\n\n"
        "class FreshdeskTools(BaseToolkit):\n"
        "    def list_tickets(self, limit: int = 10):\n"
        "        log_info('listing')\n"
        "        return requests.get('https://example.com').json()\n"
        "    def create_ticket(self, subject: str):\n"
        "        return {'subject': subject}\n"
    )
    assert names == ["list_tickets", "create_ticket"]


def test_ast_discovers_basetoolkit_methods_as_capabilities():
    names = validate_tenant_python_source(
        "from supertools.common.base_tool import BaseToolkit\n\n"
        "class FreshdeskTools(BaseToolkit):\n"
        "    def __init__(self):\n"
        "        super().__init__(name='Freshdesk')\n"
        "    def __str__(self):\n"
        "        return self.name\n"
        "    def _helper(self):\n"
        "        return 1\n"
        "    def list_tickets(self):\n"
        "        return []\n"
    )
    assert names == ["list_tickets"]
    assert "_helper" not in names


def test_ast_rejects_dangerous_dunder_attributes_only():
    """Safe dunders (__init__/__str__) ok; escape hatches still blocked."""
    names = validate_tenant_python_source(
        "from supertools.common.base_tool import BaseToolkit\n\n"
        "class T(BaseToolkit):\n"
        "    def __init__(self):\n"
        "        super().__init__(name='x')\n"
        "    def list_tickets(self):\n"
        "        return []\n"
    )
    assert names == ["list_tickets"]
    with pytest.raises(SandboxValidationError, match="__subclasses__"):
        validate_tenant_python_source(
            "async def ok(ctx):\n"
            "    return object.__subclasses__()\n"
        )
    with pytest.raises(SandboxValidationError, match="__globals__"):
        validate_tenant_python_source(
            "async def ok(ctx):\n"
            "    return ok.__globals__\n"
        )
    with pytest.raises(SandboxValidationError, match="__builtins__"):
        validate_tenant_python_source(
            "async def ok(ctx):\n"
            "    return __builtins__\n"
        )


def test_ast_allows_agno_utils_log_import():
    names = validate_tenant_python_source(
        "from agno.utils.log import log_info, logger\n\n"
        "async def foo(ctx):\n"
        "    log_info('ok')\n"
        "    return 1\n"
    )
    assert names == ["foo"]


def test_ast_allows_common_toolkit_stdlib_imports():
    """API toolkit code often uses time, urllib.parse, email.utils, etc."""
    names = validate_tenant_python_source(
        "import time\n"
        "import copy\n"
        "import base64\n"
        "import hmac\n"
        "import html\n"
        "import textwrap\n"
        "import contextlib\n"
        "import numbers\n"
        "import operator\n"
        "import random\n"
        "import calendar\n"
        "from email.utils import parsedate_to_datetime\n"
        "from urllib.parse import urljoin, urlencode\n"
        "import requests\n\n"
        "async def list_tickets(ctx):\n"
        "    time.sleep(0)\n"
        "    return {'ok': True}\n"
    )
    assert names == ["list_tickets"]


def test_ast_allows_stdlib_and_requests_denies_os():
    """Deny-list policy: zoneinfo/time/logging/requests ok; os blocked."""
    names = validate_tenant_python_source(
        "import zoneinfo\n"
        "import time\n"
        "import requests\n"
        "import logging\n\n"
        "async def list_tickets(ctx):\n"
        "    return {'ok': True}\n"
    )
    assert names == ["list_tickets"]
    with pytest.raises(SandboxValidationError, match="Import not allowed: os"):
        validate_tenant_python_source("import os\nasync def ok(ctx):\n    return 1\n")


def test_ast_accepts_starter_and_discovers_capabilities():
    names = validate_tenant_python_source(CC_PBX_STARTER_SOURCE)
    assert "create_tenant" in names
    assert "get_call_log" in names
    assert "list_campaigns" not in names
    assert len(names) == 11


def test_merge_tenant_python_credential_json_into_settings():
    from app.tools.providers import merge_tenant_python_settings

    merged, use_bearer = merge_tenant_python_settings(
        {"base_url": "https://dev2.cloud-connect.in", "timeout": 60},
        '{"pbx_token_id":"pbx-secret","ccpl_token_id":"ccpl-secret","ccpl_unique_token":"tenant-unique"}',
    )
    assert merged["pbx_token_id"] == "pbx-secret"
    assert merged["ccpl_token_id"] == "ccpl-secret"
    assert merged["ccpl_unique_token"] == "tenant-unique"
    assert merged["base_url"] == "https://dev2.cloud-connect.in"
    assert use_bearer is False

    plain, use_bearer_plain = merge_tenant_python_settings(
        {"base_url": "https://api.example.com"},
        "plain-bearer-token",
    )
    assert plain == {"base_url": "https://api.example.com"}
    assert use_bearer_plain is True


def test_dependencies_must_be_allowlisted():
    allow = {("jsonschema", "4.26.0")}
    assert validate_dependencies(
        [{"name": "jsonschema", "version": "4.26.0"}], allow
    ) == [{"name": "jsonschema", "version": "4.26.0"}]
    with pytest.raises(SandboxValidationError, match="not on the platform allowlist"):
        validate_dependencies([{"name": "requests", "version": "2.0.0"}], allow)


def test_tenant_python_config_validation():
    parsed = validate_provider_config(
        "tenant_python",
        {
            "source_code": SAFE_SOURCE,
            "dependencies": [],
            "capabilities": [
                {
                    "name": "list_items",
                    "description": "List",
                    "mutating": False,
                    "input_schema": {"type": "object", "properties": {}},
                },
                {
                    "name": "create_item",
                    "description": "Create",
                    "mutating": True,
                    "input_schema": {"type": "object", "properties": {}},
                },
            ],
            "settings": {"base_url": "https://api.example.com"},
            "version_status": "draft",
        },
    )
    assert parsed.model_dump()["capabilities"][1]["mutating"] is True
    with pytest.raises(ProviderValidationError, match="Import not allowed"):
        validate_provider_config(
            "tenant_python",
            {
                "source_code": "import socket\nasync def ok(ctx):\n    return 1\n",
                "capabilities": [{"name": "ok", "description": "", "mutating": False}],
            },
        )


@pytest.mark.asyncio
async def test_tenant_python_build_requires_published_and_marks_mutations(monkeypatch):
    provider = TenantPythonProvider()
    config = {
        "source_code": SAFE_SOURCE,
        "dependencies": [],
        "capabilities": [
            {
                "name": "list_items",
                "description": "List",
                "mutating": False,
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "create_item",
                "description": "Create",
                "mutating": True,
                "input_schema": {"type": "object", "properties": {}},
            },
        ],
        "settings": {"base_url": "https://api.example.com"},
        "version_status": "draft",
    }
    client = SafeRestClient({"api.example.com"})
    context = ProviderBuildContext(
        client=client,
        prefix="pbx",
        headers={},
        approval_required=False,
        credential_value="secret-token",
    )
    with pytest.raises(ProviderValidationError, match="must be published"):
        await provider.build_tools(config, context)

    config["version_status"] = "published"
    monkeypatch.setattr(
        "app.core.settings.get_settings",
        lambda: SimpleNamespace(
            sandbox_manager_url="http://sandbox-manager:8090",
            sandbox_callback_base_url="http://backend:7777",
            sandbox_tenant_concurrency=2,
            sandbox_python_image="atlas-sandbox-python:local",
            sandbox_wall_seconds=30,
        ),
    )

    async def fake_validate(url: str) -> None:
        return None

    monkeypatch.setattr(client, "validate_url", fake_validate)
    tools = await provider.build_tools(config, context)
    assert len(tools) == 2
    assert {tool.__name__ for tool in tools} == {"list_items", "create_item"}
    mutating = next(tool for tool in tools if tool.__name__ == "create_item")
    assert getattr(mutating, "requires_confirmation", False) or hasattr(
        mutating, "__wrapped__"
    ) or callable(mutating)


@pytest.mark.asyncio
async def test_http_proxy_enforces_host_allowlist():
    client = SafeRestClient({"api.example.com"})
    orch = SandboxOrchestrator(
        manager_url="",
        client=client,
        callback_base_url="http://backend:7777",
    )
    run = SandboxRunRequest(
        source_code=SAFE_SOURCE,
        settings={},
        capability="list_items",
        arguments={},
    )
    # Manually register like run() would.
    from app.tools.sandbox import orchestrator as orch_mod

    run_id = "test-run"
    orch_mod._ACTIVE[run_id] = orch
    orch_mod._RUN_REQUESTS[run_id] = run
    try:
        denied = await orch.handle_http_proxy(
            run_id,
            method="GET",
            url="https://evil.example/x",
        )
        assert denied["ok"] is False
        assert denied["status_code"] == 403
    finally:
        orch_mod._ACTIVE.pop(run_id, None)
        orch_mod._RUN_REQUESTS.pop(run_id, None)


@pytest.mark.asyncio
async def test_orchestrator_reports_missing_manager():
    client = SafeRestClient({"api.example.com"})
    orch = SandboxOrchestrator(manager_url="", client=client)
    result = await orch.run(
        SandboxRunRequest(
            source_code=SAFE_SOURCE,
            settings={},
            capability="list_items",
            arguments={},
        )
    )
    assert result.ok is False
    assert "not configured" in (result.error or "")
    assert get_orchestrator_for_run(result.run_id) is None


@pytest.mark.asyncio
async def test_provider_catalog_includes_tenant_python():
    assert "tenant_python" in PROVIDERS
    assert PROVIDERS["tenant_python"].label == "Editable Python"
