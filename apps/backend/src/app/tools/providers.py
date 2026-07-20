from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

import yaml
from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate as validate_json
from pydantic import BaseModel, Field, TypeAdapter, field_validator, model_validator

from app.tools.custom import CUSTOM_TOOL_BY_KEY, public_custom_tool_catalog
from app.tools.custom.base import CustomToolContext
from app.tools.registry import SafeRestClient, approval, build_definition_tool
from app.tools.toolkit_catalog import (
    TOOLKIT_BY_KEY,
    ToolkitSpec,
    public_toolkit_catalog,
    toolkit_availability,
)

MAX_DOCUMENT_BYTES = 1_000_000
MAX_TOOLS = 100
SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,99}$")
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
DESTRUCTIVE_NAME_PARTS = {
    "create",
    "delete",
    "execute",
    "publish",
    "remove",
    "send",
    "update",
    "upload",
    "write",
}


class ProviderValidationError(ValueError):
    pass


class Capability(BaseModel):
    name: str
    description: str = ""
    approval_required: bool = False
    input_schema: dict[str, Any] = Field(default_factory=dict)


class ConnectionResult(BaseModel):
    ok: bool
    message: str
    capabilities: list[Capability] = Field(default_factory=list)


class HttpConfig(BaseModel):
    base_url: str = Field(max_length=2048)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    path: str = Field(default="", max_length=2048)
    request_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    response_description: str | None = Field(default=None, max_length=4000)
    response_schema: dict[str, Any] | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    credential_header: str = "Authorization"
    credential_prefix: str = "Bearer "
    timeout_seconds: float = Field(default=10, ge=1, le=30)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        _validate_remote_url(value)
        parsed = urlsplit(value)
        if parsed.query or parsed.fragment:
            raise ValueError("base_url cannot contain query parameters or a fragment")
        return value.rstrip("/")

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if "://" in value or value.startswith("//") or ".." in value.split("/"):
            raise ValueError("path must be relative and cannot contain traversal")
        return value

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, value: dict[str, str]) -> dict[str, str]:
        _validate_headers(value)
        return value

    @field_validator("request_schema")
    @classmethod
    def validate_request_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_object_schema(value)
        return value


class OpenAPIConfig(BaseModel):
    source_url: str | None = Field(default=None, max_length=2048)
    document: str | dict[str, Any] | None = None
    allowed_operations: list[str] = Field(default_factory=list, max_length=MAX_TOOLS)
    base_url_override: str | None = Field(default=None, max_length=2048)
    headers: dict[str, str] = Field(default_factory=dict)
    credential_header: str = "Authorization"
    credential_prefix: str = "Bearer "
    timeout_seconds: float = Field(default=10, ge=1, le=30)

    @model_validator(mode="after")
    def exactly_one_source(self) -> OpenAPIConfig:
        if (self.source_url is None) == (self.document is None):
            raise ValueError("Exactly one of source_url or document is required")
        if self.source_url:
            _validate_remote_url(self.source_url)
        if self.base_url_override:
            _validate_remote_url(self.base_url_override)
        if len(set(self.allowed_operations)) != len(self.allowed_operations):
            raise ValueError("allowed_operations cannot contain duplicates")
        for operation in self.allowed_operations:
            if not SAFE_NAME.fullmatch(operation):
                raise ValueError(f"Invalid operation name: {operation}")
        _validate_headers(self.headers)
        if isinstance(self.document, str) and len(self.document.encode()) > MAX_DOCUMENT_BYTES:
            raise ValueError("OpenAPI document is too large")
        return self


class PythonToolkitConfig(BaseModel):
    toolkit: str
    options: dict[str, Any] = Field(default_factory=dict)
    include_tools: list[str] = Field(default_factory=list, max_length=MAX_TOOLS)
    destructive_tools: list[str] = Field(default_factory=list, max_length=MAX_TOOLS)

    @model_validator(mode="after")
    def safe_options(self) -> PythonToolkitConfig:
        spec = TOOLKIT_BY_KEY.get(self.toolkit)
        if spec is None or not spec.exposed or spec.tier == "blocked":
            raise ValueError("Toolkit is not in the Atlas allowlist")
        unknown = set(self.options) - set(spec.options)
        if unknown:
            raise ValueError(f"Unsupported options for {self.toolkit}: {sorted(unknown)}")
        for name, value in self.options.items():
            schema = spec.options[name]
            if schema.get("type") == "integer":
                if not isinstance(value, int):
                    raise ValueError(f"{name} must be an integer")
                if value < schema.get("minimum", value) or value > schema.get("maximum", value):
                    raise ValueError(
                        f"{name} must be between {schema.get('minimum')} and "
                        f"{schema.get('maximum')}"
                    )
        return self


class CustomPythonConfig(BaseModel):
    custom_tool: str
    settings: dict[str, Any] = Field(default_factory=dict)
    include_tools: list[str] = Field(default_factory=list, max_length=MAX_TOOLS)
    destructive_tools: list[str] = Field(default_factory=list, max_length=MAX_TOOLS)

    @model_validator(mode="after")
    def registered_source_only(self) -> CustomPythonConfig:
        spec = CUSTOM_TOOL_BY_KEY.get(self.custom_tool)
        if spec is None:
            raise ValueError("Custom Python tool is not in the source-controlled registry")
        unknown_settings = set(self.settings) - set(spec.settings_model.model_fields)
        if unknown_settings:
            raise ValueError(
                f"Unsupported settings for {self.custom_tool}: "
                f"{sorted(unknown_settings)}"
            )
        parsed_settings = spec.settings_model.model_validate(self.settings)
        self.settings = parsed_settings.model_dump(mode="json", exclude_none=True)
        available = {capability.name for capability in spec.capabilities}
        for name in self.include_tools + self.destructive_tools:
            if name not in available:
                raise ValueError(f"Unknown custom tool capability: {name}")
        if len(set(self.include_tools)) != len(self.include_tools):
            raise ValueError("include_tools cannot contain duplicates")
        if len(set(self.destructive_tools)) != len(self.destructive_tools):
            raise ValueError("destructive_tools cannot contain duplicates")
        return self


class TenantPythonCapability(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=2000)
    mutating: bool = False
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not re.fullmatch(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$", value):
            raise ValueError(f"Invalid capability name: {value}")
        return value


class TenantPythonDependency(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=64)


class TenantPythonConfig(BaseModel):
    source_code: str = Field(min_length=1, max_length=80_000)
    dependencies: list[TenantPythonDependency] = Field(default_factory=list, max_length=50)
    capabilities: list[TenantPythonCapability] = Field(default_factory=list, max_length=MAX_TOOLS)
    settings: dict[str, Any] = Field(default_factory=dict)
    template: str | None = Field(default=None, max_length=64)
    version_status: Literal["draft", "validated", "published"] = "draft"

    @model_validator(mode="after")
    def validate_source_and_caps(self) -> TenantPythonConfig:
        from app.tools.sandbox.validator import (
            SandboxValidationError,
            validate_tenant_python_source,
        )

        try:
            discovered = set(validate_tenant_python_source(self.source_code))
        except SandboxValidationError as exc:
            raise ValueError(str(exc)) from exc
        if not self.capabilities:
            self.capabilities = [
                TenantPythonCapability(name=name, description=name.replace("_", " "))
                for name in sorted(discovered)
            ]
        declared = {item.name for item in self.capabilities}
        missing = declared - discovered
        if missing:
            raise ValueError(
                "Declared capabilities missing from source: " + ", ".join(sorted(missing))
            )
        if len(declared) != len(self.capabilities):
            raise ValueError("capabilities cannot contain duplicates")
        return self


class MCPConfig(BaseModel):
    transport: Literal["streamable-http", "sse"] = "streamable-http"
    url: str = Field(max_length=2048)
    include_tools: list[str] = Field(default_factory=list, max_length=MAX_TOOLS)
    exclude_tools: list[str] = Field(default_factory=list, max_length=MAX_TOOLS)
    destructive_tools: list[str] = Field(default_factory=list, max_length=MAX_TOOLS)
    credential_header: str = "Authorization"
    credential_prefix: str = "Bearer "
    timeout_seconds: int = Field(default=10, ge=1, le=30)

    @model_validator(mode="after")
    def validate_mcp(self) -> MCPConfig:
        _validate_remote_url(self.url)
        if set(self.include_tools) & set(self.exclude_tools):
            raise ValueError("A tool cannot be both included and excluded")
        for name in self.include_tools + self.exclude_tools + self.destructive_tools:
            if not SAFE_NAME.fullmatch(name):
                raise ValueError(f"Invalid MCP tool name: {name}")
        _validate_credential_header(self.credential_header)
        return self


PROVIDER_CONFIG_ADAPTERS: dict[str, TypeAdapter[Any]] = {
    "http": TypeAdapter(HttpConfig),
    "openapi": TypeAdapter(OpenAPIConfig),
    "python_toolkit": TypeAdapter(PythonToolkitConfig),
    "custom_python": TypeAdapter(CustomPythonConfig),
    "tenant_python": TypeAdapter(TenantPythonConfig),
    "mcp": TypeAdapter(MCPConfig),
}


def validate_provider_config(provider: str, config: Mapping[str, Any]) -> BaseModel:
    adapter = PROVIDER_CONFIG_ADAPTERS.get(provider)
    if adapter is None:
        raise ProviderValidationError(f"Unsupported tool provider: {provider}")
    try:
        return adapter.validate_python(dict(config))
    except ValueError as exc:
        raise ProviderValidationError(str(exc)) from exc


def is_destructive_name(name: str) -> bool:
    parts = set(re.split(r"[^a-z0-9]+", name.lower()))
    return bool(parts & DESTRUCTIVE_NAME_PARTS)


@dataclass(slots=True)
class ProviderBuildContext:
    client: SafeRestClient
    prefix: str
    headers: dict[str, str]
    approval_required: bool
    credential_provider: str | None = None
    credential_value: str | None = None


class ToolProvider(Protocol):
    key: str
    label: str

    def validate_config(self, config: Mapping[str, Any]) -> BaseModel: ...

    async def test_connection(
        self, config: Mapping[str, Any], client: SafeRestClient, headers: dict[str, str]
    ) -> ConnectionResult: ...

    async def enumerate_tools(
        self, config: Mapping[str, Any], client: SafeRestClient, headers: dict[str, str]
    ) -> list[Capability]: ...

    async def build_tools(
        self, config: Mapping[str, Any], context: ProviderBuildContext
    ) -> list[Any]: ...

    def redact_config(self, config: Mapping[str, Any]) -> dict[str, Any]: ...


class BaseProvider:
    key = ""
    label = ""

    def validate_config(self, config: Mapping[str, Any]) -> BaseModel:
        return validate_provider_config(self.key, config)

    def redact_config(self, config: Mapping[str, Any]) -> dict[str, Any]:
        # Provider config never contains credential values. Keep a defensive
        # redaction in case a legacy row predates schema validation.
        return {
            key: (
                "[redacted]"
                if any(part in key.lower() for part in ("secret", "token", "password"))
                else value
            )
            for key, value in config.items()
        }


class HttpProvider(BaseProvider):
    key = "http"
    label = "HTTP Request"

    async def enumerate_tools(
        self, config: Mapping[str, Any], client: SafeRestClient, headers: dict[str, str]
    ) -> list[Capability]:
        parsed = HttpConfig.model_validate(config)
        await client.validate_url(parsed.base_url)
        return [
            Capability(
                name="request",
                description=parsed.response_description or f"{parsed.method} {parsed.path}",
                approval_required=parsed.method in MUTATING_METHODS,
                input_schema=parsed.request_schema,
            )
        ]

    async def test_connection(
        self, config: Mapping[str, Any], client: SafeRestClient, headers: dict[str, str]
    ) -> ConnectionResult:
        capabilities = await self.enumerate_tools(config, client, headers)
        return ConnectionResult(
            ok=True, message="Target URL passed outbound safety checks", capabilities=capabilities
        )

    async def build_tools(
        self, config: Mapping[str, Any], context: ProviderBuildContext
    ) -> list[Any]:
        parsed = HttpConfig.model_validate(config)
        headers = parsed.headers | context.headers
        return [
            build_definition_tool(
                context.client,
                name=context.prefix,
                description=parsed.response_description or f"Call {parsed.method} {parsed.path}",
                method=parsed.method,
                base_url=parsed.base_url,
                path_template=parsed.path,
                request_schema=parsed.request_schema,
                headers=headers,
                approval_required=context.approval_required,
            )
        ]


class OpenAPIProvider(BaseProvider):
    key = "openapi"
    label = "OpenAPI"

    async def _document(self, parsed: OpenAPIConfig, client: SafeRestClient) -> dict[str, Any]:
        raw: str | dict[str, Any]
        if parsed.source_url:
            response = await client.request("GET", parsed.source_url)
            raw = response["body"]
        else:
            assert parsed.document is not None
            raw = parsed.document
        if isinstance(raw, str):
            if len(raw.encode()) > MAX_DOCUMENT_BYTES:
                raise ProviderValidationError("OpenAPI document is too large")
            try:
                document = yaml.safe_load(raw)
            except yaml.YAMLError as exc:
                raise ProviderValidationError("OpenAPI document is not valid JSON/YAML") from exc
        else:
            document = raw
        if not isinstance(document, dict) or not str(document.get("openapi", "")).startswith("3."):
            raise ProviderValidationError("Only OpenAPI 3.x documents are supported")
        if len(document.get("paths", {})) > MAX_TOOLS:
            raise ProviderValidationError("OpenAPI document contains too many paths")
        return document

    async def _operations(
        self, parsed: OpenAPIConfig, client: SafeRestClient
    ) -> list[tuple[Capability, str, str, dict[str, Any], str]]:
        document = await self._document(parsed, client)
        servers = document.get("servers") or []
        base_url = parsed.base_url_override
        if not base_url and servers and isinstance(servers[0], dict):
            base_url = servers[0].get("url")
        if not isinstance(base_url, str):
            raise ProviderValidationError("OpenAPI document must define an absolute server URL")
        await client.validate_url(base_url)
        operations: list[tuple[Capability, str, str, dict[str, Any], str]] = []
        for path, path_item in document.get("paths", {}).items():
            if not isinstance(path, str) or not isinstance(path_item, dict):
                continue
            for method in ("get", "post", "put", "patch", "delete"):
                operation = path_item.get(method)
                if not isinstance(operation, dict):
                    continue
                operation_id = operation.get("operationId")
                if not isinstance(operation_id, str) or not SAFE_NAME.fullmatch(operation_id):
                    continue
                request_schema = _openapi_request_schema(path_item, operation)
                capability = Capability(
                    name=operation_id,
                    description=str(operation.get("description") or operation.get("summary") or ""),
                    approval_required=method.upper() in MUTATING_METHODS,
                    input_schema=request_schema,
                )
                operations.append((capability, method.upper(), path, request_schema, base_url))
                if len(operations) > MAX_TOOLS:
                    raise ProviderValidationError("OpenAPI document contains too many operations")
        return operations

    async def enumerate_tools(
        self, config: Mapping[str, Any], client: SafeRestClient, headers: dict[str, str]
    ) -> list[Capability]:
        parsed = OpenAPIConfig.model_validate(config)
        return [item[0] for item in await self._operations(parsed, client)]

    async def test_connection(
        self, config: Mapping[str, Any], client: SafeRestClient, headers: dict[str, str]
    ) -> ConnectionResult:
        capabilities = await self.enumerate_tools(config, client, headers)
        return ConnectionResult(
            ok=True,
            message=f"Validated OpenAPI document with {len(capabilities)} reviewable operations",
            capabilities=capabilities,
        )

    async def build_tools(
        self, config: Mapping[str, Any], context: ProviderBuildContext
    ) -> list[Any]:
        parsed = OpenAPIConfig.model_validate(config)
        if not parsed.allowed_operations:
            raise ProviderValidationError("Select at least one reviewed OpenAPI operation")
        selected = set(parsed.allowed_operations)
        operations = await self._operations(parsed, context.client)
        available = {item[0].name for item in operations}
        if not selected <= available:
            raise ProviderValidationError("Selected OpenAPI operation no longer exists")
        headers = parsed.headers | context.headers
        tools: list[Any] = []
        for capability, method, path, request_schema, base_url in operations:
            if capability.name not in selected:
                continue
            tools.append(
                build_definition_tool(
                    context.client,
                    name=f"{context.prefix}_{_safe_callable_name(capability.name)}",
                    description=capability.description,
                    method=method,
                    base_url=base_url,
                    path_template=path,
                    request_schema=request_schema,
                    headers=headers,
                    approval_required=context.approval_required or capability.approval_required,
                )
            )
        return tools


class PythonToolkitProvider(BaseProvider):
    key = "python_toolkit"
    label = "Python Toolkit"

    async def enumerate_tools(
        self, config: Mapping[str, Any], client: SafeRestClient, headers: dict[str, str]
    ) -> list[Capability]:
        parsed = PythonToolkitConfig.model_validate(config)
        spec = TOOLKIT_BY_KEY[parsed.toolkit]
        available, reason = toolkit_availability(spec)
        if not available:
            raise ProviderValidationError(reason or "Toolkit is unavailable")
        if spec.credentials:
            return [
                Capability(
                    name="credential_gated",
                    description=(
                        "Capabilities load only after the matching tenant credential is supplied."
                    ),
                    approval_required=spec.side_effects,
                )
            ]
        toolkit = _instantiate_toolkit(spec, parsed.options, None)
        return _toolkit_capabilities(toolkit, spec)

    async def test_connection(
        self, config: Mapping[str, Any], client: SafeRestClient, headers: dict[str, str]
    ) -> ConnectionResult:
        capabilities = await self.enumerate_tools(config, client, headers)
        spec = TOOLKIT_BY_KEY[PythonToolkitConfig.model_validate(config).toolkit]
        return ConnectionResult(
            ok=True,
            message=(
                "Allowlisted toolkit is installed; a matching tenant credential is required"
                if spec.credentials
                else "Allowlisted server-side toolkit is available"
            ),
            capabilities=capabilities,
        )

    async def build_tools(
        self, config: Mapping[str, Any], context: ProviderBuildContext
    ) -> list[Any]:
        parsed = PythonToolkitConfig.model_validate(config)
        spec = TOOLKIT_BY_KEY[parsed.toolkit]
        available, reason = toolkit_availability(spec)
        if not available:
            raise ProviderValidationError(reason or "Toolkit is unavailable")
        credential_value: str | None = None
        if spec.credentials:
            required = spec.credentials[0]
            if context.credential_value is None:
                raise ProviderValidationError(
                    f"{spec.label} requires a {required.provider} tenant credential"
                )
            if context.credential_provider != required.provider:
                raise ProviderValidationError(
                    f"{spec.label} requires credential provider '{required.provider}'"
                )
            credential_value = context.credential_value
        toolkit = _instantiate_toolkit(spec, parsed.options, credential_value)
        include = set(parsed.include_tools)
        if include:
            toolkit.functions = {
                name: function for name, function in toolkit.functions.items() if name in include
            }
        _prefix_toolkit(
            toolkit, context.prefix, context.approval_required, parsed.destructive_tools
        )
        return [toolkit]


class CustomPythonProvider(BaseProvider):
    key = "custom_python"
    label = "Custom Python"

    async def _capabilities(
        self,
        parsed: CustomPythonConfig,
        client: SafeRestClient,
    ) -> list[Capability]:
        spec = CUSTOM_TOOL_BY_KEY[parsed.custom_tool]
        settings = spec.settings_model.model_validate(parsed.settings)
        for field_name in spec.url_fields:
            value = getattr(settings, field_name, None)
            if not isinstance(value, str):
                raise ProviderValidationError(
                    f"Registered URL field is missing: {field_name}"
                )
            await client.validate_url(value)
        selected = set(parsed.include_tools)
        return [
            Capability(
                name=item.name,
                description=item.description,
                input_schema=item.input_schema,
                approval_required=item.mutating
                or item.name in parsed.destructive_tools,
            )
            for item in spec.capabilities
            if not selected or item.name in selected
        ]

    async def enumerate_tools(
        self, config: Mapping[str, Any], client: SafeRestClient, headers: dict[str, str]
    ) -> list[Capability]:
        del headers
        parsed = CustomPythonConfig.model_validate(config)
        return await self._capabilities(parsed, client)

    async def test_connection(
        self, config: Mapping[str, Any], client: SafeRestClient, headers: dict[str, str]
    ) -> ConnectionResult:
        capabilities = await self.enumerate_tools(config, client, headers)
        return ConnectionResult(
            ok=True,
            message=(
                "Source-controlled tool is registered and all configured URLs "
                "passed outbound safety checks"
            ),
            capabilities=capabilities,
        )

    async def build_tools(
        self, config: Mapping[str, Any], context: ProviderBuildContext
    ) -> list[Any]:
        parsed = CustomPythonConfig.model_validate(config)
        spec = CUSTOM_TOOL_BY_KEY[parsed.custom_tool]
        if spec.credential_provider:
            if context.credential_value is None:
                raise ProviderValidationError(
                    f"{spec.label} requires a {spec.credential_provider} tenant credential"
                )
            if context.credential_provider != spec.credential_provider:
                raise ProviderValidationError(
                    f"{spec.label} requires credential provider "
                    f"'{spec.credential_provider}'"
                )
        capabilities = await self._capabilities(parsed, context.client)
        capability_by_name = {item.name: item for item in capabilities}
        settings = spec.settings_model.model_validate(parsed.settings)
        built = spec.build(
            CustomToolContext(
                client=context.client,
                settings=settings,
                credential_value=context.credential_value,
            )
        )
        functions: list[Any] = []
        for function in built:
            old_name = function.__name__
            capability = capability_by_name.get(old_name)
            if capability is None:
                if parsed.include_tools:
                    continue
                raise ProviderValidationError(
                    f"Custom builder returned unregistered capability: {old_name}"
                )
            function.__name__ = f"{context.prefix}_{_safe_callable_name(old_name)}"
            if context.approval_required or capability.approval_required:
                function = approval(function)
            functions.append(function)
        if set(capability_by_name) != {
            function.__name__.removeprefix(f"{context.prefix}_")
            for function in functions
        }:
            raise ProviderValidationError(
                "Custom builder capabilities do not match its registry declaration"
            )
        return functions


class TenantPythonProvider(BaseProvider):
    key = "tenant_python"
    label = "Editable Python"

    async def enumerate_tools(
        self, config: Mapping[str, Any], client: SafeRestClient, headers: dict[str, str]
    ) -> list[Capability]:
        del client, headers
        parsed = TenantPythonConfig.model_validate(config)
        return [
            Capability(
                name=item.name,
                description=item.description,
                approval_required=item.mutating,
                input_schema=item.input_schema,
            )
            for item in parsed.capabilities
        ]

    async def test_connection(
        self, config: Mapping[str, Any], client: SafeRestClient, headers: dict[str, str]
    ) -> ConnectionResult:
        capabilities = await self.enumerate_tools(config, client, headers)
        base_url = str((config.get("settings") or {}).get("base_url") or "")
        if base_url:
            await client.validate_url(base_url)
        return ConnectionResult(
            ok=True,
            message="Editable Python source passed AST checks; outbound hosts validated",
            capabilities=capabilities,
        )

    async def build_tools(
        self, config: Mapping[str, Any], context: ProviderBuildContext
    ) -> list[Any]:
        from app.core.settings import get_settings
        from app.tools.sandbox.orchestrator import SandboxOrchestrator

        parsed = TenantPythonConfig.model_validate(config)
        if parsed.version_status != "published":
            raise ProviderValidationError(
                "Editable Python tool must be published before agents can use it"
            )
        settings = get_settings()
        base_url = str(parsed.settings.get("base_url") or "")
        if base_url:
            await context.client.validate_url(base_url)

        orchestrator = SandboxOrchestrator(
            manager_url=settings.sandbox_manager_url,
            client=context.client,
            callback_base_url=settings.sandbox_callback_base_url,
            concurrency_limit=settings.sandbox_tenant_concurrency,
            image=settings.sandbox_python_image,
        )
        proxy_headers = dict(context.headers)
        if context.credential_value and "Authorization" not in proxy_headers:
            proxy_headers["Authorization"] = f"Bearer {context.credential_value}"

        return [
            self._make_capability_tool(
                orchestrator=orchestrator,
                parsed=parsed,
                capability=capability,
                prefix=context.prefix,
                headers=proxy_headers,
                force_approval=context.approval_required,
                wall_seconds=settings.sandbox_wall_seconds,
            )
            for capability in parsed.capabilities
        ]

    def _make_capability_tool(
        self,
        *,
        orchestrator: Any,
        parsed: TenantPythonConfig,
        capability: TenantPythonCapability,
        prefix: str,
        headers: dict[str, str],
        force_approval: bool,
        wall_seconds: int,
    ) -> Any:
        from app.tools.sandbox.orchestrator import SandboxRunRequest

        async def _runner(**kwargs: Any) -> Any:
            result = await orchestrator.run(
                SandboxRunRequest(
                    source_code=parsed.source_code,
                    settings=dict(parsed.settings),
                    capability=capability.name,
                    arguments=dict(kwargs),
                    headers=headers,
                    timeout_seconds=wall_seconds,
                )
            )
            if not result.ok:
                raise RuntimeError(result.error or "Sandbox run failed")
            return result.value

        _runner.__name__ = f"{prefix}_{_safe_callable_name(capability.name)}"
        _runner.__doc__ = capability.description or capability.name
        if force_approval or capability.mutating:
            return approval(_runner)
        return _runner


class MCPProvider(BaseProvider):
    key = "mcp"
    label = "MCP Server"

    def _toolkit(self, parsed: MCPConfig, headers: dict[str, str], prefix: str) -> Any:
        from agno.tools.mcp import MCPTools, SSEClientParams, StreamableHTTPClientParams

        params: Any
        if parsed.transport == "sse":
            params = SSEClientParams(
                url=parsed.url,
                headers=headers,
                timeout=float(parsed.timeout_seconds),
                sse_read_timeout=float(parsed.timeout_seconds),
            )
        else:
            from datetime import timedelta

            timeout = timedelta(seconds=parsed.timeout_seconds)
            params = StreamableHTTPClientParams(
                url=parsed.url,
                headers=headers,
                timeout=timeout,
                sse_read_timeout=timeout,
                terminate_on_close=True,
            )
        approvals = [
            name
            for name in parsed.include_tools
            if name in parsed.destructive_tools or is_destructive_name(name)
        ]
        return MCPTools(
            transport=parsed.transport,
            server_params=params,
            timeout_seconds=parsed.timeout_seconds,
            include_tools=parsed.include_tools,
            exclude_tools=parsed.exclude_tools,
            requires_confirmation_tools=approvals,
            tool_name_prefix=prefix,
            refresh_connection=True,
        )

    async def enumerate_tools(
        self, config: Mapping[str, Any], client: SafeRestClient, headers: dict[str, str]
    ) -> list[Capability]:
        parsed = MCPConfig.model_validate(config)
        await client.validate_url(parsed.url)
        # Discovery intentionally ignores include filters so admins can review the server.
        discovery = parsed.model_copy(
            update={"include_tools": parsed.include_tools or ["__discovery__"]}
        )
        toolkit = self._toolkit(discovery, headers, "preview")
        toolkit.include_tools = None
        try:
            await toolkit.connect()
            return [
                Capability(
                    name=function.name.removeprefix("preview_"),
                    description=function.description or "",
                    approval_required=is_destructive_name(function.name),
                    input_schema=function.parameters or {},
                )
                for function in list(toolkit.functions.values())[:MAX_TOOLS]
            ]
        finally:
            await toolkit.close()

    async def test_connection(
        self, config: Mapping[str, Any], client: SafeRestClient, headers: dict[str, str]
    ) -> ConnectionResult:
        capabilities = await self.enumerate_tools(config, client, headers)
        return ConnectionResult(
            ok=True,
            message=f"Connected and discovered {len(capabilities)} MCP tools",
            capabilities=capabilities,
        )

    async def build_tools(
        self, config: Mapping[str, Any], context: ProviderBuildContext
    ) -> list[Any]:
        parsed = MCPConfig.model_validate(config)
        if not parsed.include_tools:
            raise ProviderValidationError("Select at least one reviewed MCP tool")
        await context.client.validate_url(parsed.url)
        toolkit = self._toolkit(parsed, context.headers, context.prefix)
        if context.approval_required:
            toolkit.requires_confirmation_tools = list(parsed.include_tools)
        return [toolkit]


PROVIDERS: dict[str, ToolProvider] = {
    provider.key: provider
    for provider in (
        HttpProvider(),
        OpenAPIProvider(),
        PythonToolkitProvider(),
        CustomPythonProvider(),
        TenantPythonProvider(),
        MCPProvider(),
    )
}


def provider_catalog() -> list[dict[str, Any]]:
    return [
        {
            "key": provider.key,
            "label": provider.label,
            "enabled": True,
            "remote": provider.key in {"http", "openapi", "mcp", "tenant_python"},
        }
        for provider in PROVIDERS.values()
    ]


def toolkit_catalog() -> list[dict[str, Any]]:
    return public_toolkit_catalog()


def custom_tool_catalog() -> list[dict[str, object]]:
    return public_custom_tool_catalog()


def _instantiate_toolkit(
    spec: ToolkitSpec, options: Mapping[str, Any], credential_value: str | None
) -> Any:
    module = import_module(f"agno.tools.{spec.module}")
    toolkit_type = getattr(module, spec.class_name)
    kwargs = {str(spec.options[name].get("kwarg", name)): value for name, value in options.items()}
    if credential_value is not None:
        kwargs[spec.credentials[0].kwarg] = credential_value
    try:
        return toolkit_type(**kwargs)
    except (ImportError, ModuleNotFoundError) as exc:
        raise ProviderValidationError(spec.install_hint or str(exc)) from exc
    except TypeError as exc:
        raise ProviderValidationError(
            f"{spec.label} is incompatible with the installed Agno version"
        ) from exc


def _toolkit_capabilities(toolkit: Any, spec: ToolkitSpec) -> list[Capability]:
    return [
        Capability(
            name=name,
            description=getattr(function, "description", "") or spec.description,
            approval_required=spec.side_effects and is_destructive_name(name),
            input_schema=getattr(function, "parameters", None) or {},
        )
        for name, function in list(toolkit.functions.items())[:MAX_TOOLS]
    ]


def legacy_http_config(values: Mapping[str, Any]) -> dict[str, Any]:
    config = dict(values.get("config") or {})
    return {
        "base_url": values.get("base_url", ""),
        "method": values.get("http_method", "GET"),
        "path": values.get("path", ""),
        "request_schema": values.get("request_schema") or {"type": "object", "properties": {}},
        "response_description": values.get("response_description"),
        "response_schema": values.get("response_schema"),
        "headers": values.get("headers") or {},
        "credential_header": config.get("credential_header", "Authorization"),
        "credential_prefix": config.get("credential_prefix", "Bearer "),
        "timeout_seconds": config.get("timeout_seconds", 10),
    }


def _validate_remote_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Only HTTPS URLs without embedded credentials are supported")


def _validate_headers(headers: Mapping[str, str]) -> None:
    blocked = {"authorization", "cookie", "proxy-authorization", "x-api-key", "api-key"}
    routing = {"host", "content-length", "connection"}
    if any(name.lower() in blocked for name in headers):
        raise ValueError("Secret-bearing headers must use a TenantCredential")
    if any(name.lower() in routing for name in headers):
        raise ValueError("Routing and hop-by-hop headers are forbidden")
    if any(len(name) > 100 or len(value) > 2000 for name, value in headers.items()):
        raise ValueError("Header name or value is too long")


def _validate_credential_header(name: str) -> None:
    if name.lower() in {"host", "content-length", "connection", "cookie"} or len(name) > 100:
        raise ValueError("Unsafe credential header")


def _validate_object_schema(schema: Mapping[str, Any]) -> None:
    if schema.get("type") != "object" or not isinstance(schema.get("properties", {}), dict):
        raise ValueError("Schema root must be an object")
    if len(schema.get("properties", {})) > 100:
        raise ValueError("Schema contains too many properties")


def _openapi_request_schema(
    path_item: Mapping[str, Any], operation: Mapping[str, Any]
) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    parameters = list(path_item.get("parameters") or []) + list(operation.get("parameters") or [])
    for parameter in parameters[:100]:
        if not isinstance(parameter, dict) or "$ref" in parameter:
            continue
        name = parameter.get("name")
        schema = parameter.get("schema")
        if isinstance(name, str) and name.isidentifier() and isinstance(schema, dict):
            properties[name] = {
                key: value
                for key, value in schema.items()
                if key in {"type", "description", "default", "enum"}
            }
            if parameter.get("required"):
                required.append(name)
    body = operation.get("requestBody")
    if isinstance(body, dict):
        content = body.get("content", {})
        media = content.get("application/json", {}) if isinstance(content, dict) else {}
        schema = media.get("schema") if isinstance(media, dict) else None
        if isinstance(schema, dict) and schema.get("type") == "object":
            for name, item in list(schema.get("properties", {}).items())[:100]:
                if isinstance(name, str) and name.isidentifier() and isinstance(item, dict):
                    properties[name] = item
            required.extend(name for name in schema.get("required", []) if name in properties)
    result = {"type": "object", "properties": properties}
    if required:
        result["required"] = sorted(set(required))
    return result


def _safe_callable_name(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_]", "_", name)
    return value if value and not value[0].isdigit() else f"tool_{value}"


def _prefix_toolkit(
    toolkit: Any, prefix: str, require_all: bool, destructive_tools: list[str]
) -> None:
    renamed: dict[str, Any] = {}
    for old_name, function in toolkit.functions.items():
        new_name = f"{prefix}_{_safe_callable_name(old_name)}"
        function.name = new_name
        if require_all or old_name in destructive_tools or is_destructive_name(old_name):
            function.requires_confirmation = True
        renamed[new_name] = function
    toolkit.functions = renamed


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
    try:
        validate_json(arguments, schema)
    except JsonSchemaValidationError as exc:
        raise ProviderValidationError(
            f"Arguments do not match operation schema: {exc.message}"
        ) from exc
