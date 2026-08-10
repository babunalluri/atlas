import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.tools.custom import CUSTOM_TOOL_SPECS
from app.tools.providers import (
    MUTATING_METHODS,
    legacy_http_config,
    validate_provider_config,
)
from app.tools.toolkit_catalog import TOOLKIT_SPECS


class ToolBindingIn(BaseModel):
    tool_key: Literal["web_search", "rest_read", "rest_mutate"] | None = None
    tool_definition_id: uuid.UUID | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    credential_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def exactly_one_source(self) -> "ToolBindingIn":
        if (self.tool_key is None) == (self.tool_definition_id is None):
            raise ValueError("Exactly one of tool_key or tool_definition_id is required")
        if self.tool_definition_id and self.credential_id:
            raise ValueError("Reusable tool credentials are configured on the definition")
        allowed_overrides = {"timeout_seconds"}
        if self.tool_definition_id and set(self.config) - allowed_overrides:
            raise ValueError("Reusable tools only support timeout_seconds binding overrides")
        return self


class CatalogQuery(BaseModel):
    q: str | None = Field(default=None, max_length=200)
    status: Literal["all", "published", "draft"] = "all"
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)


class AgentCatalogItemOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    status: str
    model_id: str
    published_version: int | None = None
    updated_at: datetime


class TeamCatalogItemOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    status: str
    mode: str
    member_count: int = 0
    published_version: int | None = None
    updated_at: datetime


class WorkflowCatalogItemOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    status: str
    mode: str
    step_count: int = 0
    published_version: int | None = None
    updated_at: datetime


class AgentCatalogPageOut(BaseModel):
    items: list[AgentCatalogItemOut]
    total: int
    page: int
    page_size: int


class TeamCatalogPageOut(BaseModel):
    items: list[TeamCatalogItemOut]
    total: int
    page: int
    page_size: int


class WorkflowCatalogPageOut(BaseModel):
    items: list[WorkflowCatalogItemOut]
    total: int
    page: int
    page_size: int


SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "x-api-key",
    "api-key",
}


def _validate_json_schema(value: dict[str, Any]) -> dict[str, Any]:
    if not value:
        return {"type": "object", "properties": {}}
    if value.get("type") != "object" or not isinstance(value.get("properties", {}), dict):
        raise ValueError("Schema root must have type 'object' and object properties")
    required = value.get("required", [])
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise ValueError("Schema required must be a list of property names")
    if not set(required).issubset(value.get("properties", {})):
        raise ValueError("Every required field must exist in properties")
    allowed_types = {"string", "number", "integer", "boolean", "object", "array"}
    for name, schema in value.get("properties", {}).items():
        if not isinstance(name, str) or not isinstance(schema, dict):
            raise ValueError("Schema properties must map names to schemas")
        if schema.get("type") not in allowed_types:
            raise ValueError(f"Property {name} has an unsupported type")
    return value


class ToolDefinitionBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)
    description: str | None = Field(default=None, max_length=4000)
    kind: Literal[
        "http",
        "openapi",
        "python_toolkit",
        "custom_python",
        "tenant_python",
        "mcp",
        "rest",
        "webhook",
    ] = "http"
    # Legacy HTTP fields remain accepted during the compatibility window.
    http_method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] | None = None
    base_url: str | None = Field(default=None, max_length=2048)
    path: str | None = Field(default=None, max_length=2048)
    request_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    response_description: str | None = Field(default=None, max_length=4000)
    response_schema: dict[str, Any] | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    credential_id: uuid.UUID | None = None
    approval_required: bool = False
    active: bool = True

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_http(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if normalized.get("kind", "http") in {"rest", "webhook"}:
            normalized["kind"] = "http"
        if normalized["kind"] == "http":
            # Prefer provider config, but keep accepting the 0003 field shape.
            config = dict(normalized.get("config") or {})
            legacy = legacy_http_config(normalized)
            for key, item in legacy.items():
                if key not in config or config[key] in (None, "", {}, []):
                    config[key] = item
            # Top-level headers/base_url overrides remain authoritative for
            # compatibility callers that still send the flat HTTP shape.
            if "headers" in normalized:
                config["headers"] = normalized["headers"]
            if normalized.get("base_url"):
                config["base_url"] = normalized["base_url"]
            if normalized.get("http_method"):
                config["method"] = normalized["http_method"]
            if "path" in normalized and normalized["path"] is not None:
                config["path"] = normalized["path"]
            if normalized.get("request_schema"):
                config["request_schema"] = normalized["request_schema"]
            normalized["config"] = config
        return normalized

    @model_validator(mode="after")
    def validate_provider(self) -> "ToolDefinitionBase":
        parsed = validate_provider_config(self.kind, self.config)
        self.config = parsed.model_dump(mode="json", exclude_none=True)
        if self.kind == "http":
            self.http_method = self.config["method"]
            self.base_url = self.config["base_url"]
            self.path = self.config.get("path", "")
            self.request_schema = self.config["request_schema"]
            self.response_description = self.config.get("response_description")
            self.response_schema = self.config.get("response_schema")
            self.headers = self.config.get("headers", {})
        if self.kind == "http" and self.config["method"] in MUTATING_METHODS:
            self.approval_required = True
        return self


class ToolDefinitionCreateIn(ToolDefinitionBase):
    pass


class ToolDefinitionUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    kind: Literal[
        "http", "openapi", "python_toolkit", "custom_python", "tenant_python", "mcp"
    ] | None = None
    http_method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] | None = None
    base_url: str | None = Field(default=None, max_length=2048)
    path: str | None = Field(default=None, max_length=2048)
    request_schema: dict[str, Any] | None = None
    response_description: str | None = Field(default=None, max_length=4000)
    response_schema: dict[str, Any] | None = None
    headers: dict[str, str] | None = None
    config: dict[str, Any] | None = None
    credential_id: uuid.UUID | None = None
    approval_required: bool | None = None
    active: bool | None = None


class ToolDefinitionOut(ToolDefinitionBase):
    id: uuid.UUID
    connection_status: str = "unvalidated"
    last_validated_at: datetime | None = None
    last_validation_error: str | None = None
    published_version_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class ToolDefinitionVersionOut(BaseModel):
    id: uuid.UUID
    tool_definition_id: uuid.UUID
    version: int
    status: str
    source_code: str
    dependencies: list[dict[str, Any]]
    capabilities: list[dict[str, Any]]
    settings: dict[str, Any]
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ToolRestoreIn(BaseModel):
    """Restore a historical editable-Python tool version.

    ``as_draft=False`` (default) pins live published traffic to the snapshot
    (publishing it first when the row is still draft/validated).
    ``as_draft=True`` copies the snapshot into the editable draft without
    changing the live published pointer.
    """

    as_draft: bool = False


class PlatformPythonPackageIn(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9._-]{0,99}$")
    version: str = Field(min_length=1, max_length=64)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-fA-F0-9]{64}$")
    active: bool = True


class PlatformPythonPackageUpdateIn(BaseModel):
    sha256: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[a-fA-F0-9]{64}$"
    )
    active: bool | None = None


class PlatformPythonPackageOut(BaseModel):
    id: uuid.UUID
    name: str
    version: str
    sha256: str
    active: bool
    created_at: datetime
    updated_at: datetime


class ToolProviderCatalogOut(BaseModel):
    key: str
    label: str
    enabled: bool
    remote: bool


class ToolCapabilityOut(BaseModel):
    name: str
    description: str
    approval_required: bool
    input_schema: dict[str, Any]


class ToolValidationOut(BaseModel):
    ok: bool
    message: str
    capabilities: list[ToolCapabilityOut] = Field(default_factory=list)


FrameworkAdapter = Literal[
    "agno", "langgraph", "dspy", "claude_agent_sdk", "antigravity"
]


class AgentCreateIn(BaseModel):
    slug: str
    name: str
    description: str | None = None
    instructions: str = "You are a helpful assistant."
    model_id: str = "openai:gpt-4.1-mini"
    temperature: float = Field(default=0.2, ge=0, le=2)
    memory_mode: Literal["none", "session", "persistent_user"] = "session"
    tools: list[ToolBindingIn] = Field(default_factory=list)
    knowledge_base_id: uuid.UUID | None = None
    framework_adapter: FrameworkAdapter = "agno"
    guardrails: dict[str, bool] | None = None


class AgentUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    model_id: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    memory_mode: Literal["none", "session", "persistent_user"] | None = None
    tools: list[ToolBindingIn] | None = None
    knowledge_base_id: uuid.UUID | None = None
    framework_adapter: FrameworkAdapter | None = None
    guardrails: dict[str, bool] | None = None


class AgentVersionOut(BaseModel):
    id: uuid.UUID
    version: int
    status: str
    instructions: str
    model_id: str
    temperature: float
    memory_mode: str
    framework_adapter: FrameworkAdapter = "agno"
    created_at: datetime


class AgentVersionSummaryOut(BaseModel):
    id: uuid.UUID
    version: int
    status: str
    model_id: str
    is_live: bool
    created_at: datetime


class AgentRestoreIn(BaseModel):
    """Restore a historical agent version.

    ``as_draft=False`` (default) points live traffic at the selected snapshot.
    ``as_draft=True`` clones the snapshot into a new editable draft.
    """

    as_draft: bool = False


class AgentConfigOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str | None
    published_version_id: uuid.UUID | None
    updated_at: datetime
    tools: list[ToolBindingIn] = Field(default_factory=list)
    knowledge_base_id: uuid.UUID | None = None
    framework_adapter: FrameworkAdapter = "agno"
    guardrails: dict[str, bool] = Field(
        default_factory=lambda: {
            "prompt_injection": False,
            "pii_detection": False,
            "openai_moderation": False,
        }
    )
    draft: AgentVersionOut | None = None
    published: AgentVersionOut | None = None


class ChannelBindingIn(BaseModel):
    provider: Literal["slack", "telegram", "whatsapp"]
    credential_id: uuid.UUID
    target_type: Literal["team", "workflow"]
    target_config_id: uuid.UUID
    external_config: dict[str, Any] = Field(default_factory=dict)
    active: bool = True


class ChannelBindingUpdateIn(BaseModel):
    credential_id: uuid.UUID | None = None
    target_type: Literal["team", "workflow"] | None = None
    target_config_id: uuid.UUID | None = None
    external_config: dict[str, Any] | None = None
    active: bool | None = None


class ChannelBindingOut(BaseModel):
    id: uuid.UUID
    provider: str
    credential_id: uuid.UUID
    target_type: str
    target_config_id: uuid.UUID
    external_config: dict[str, Any]
    active: bool
    created_at: datetime
    updated_at: datetime


class TeamCreateIn(BaseModel):
    slug: str
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    instructions: str = "Coordinate the team specialists and return one clear answer."
    mode: Literal["route", "coordinate"] = "coordinate"
    model_id: str = "openai:gpt-4.1-mini"
    temperature: float = Field(default=0.2, ge=0, le=2)
    member_config_ids: list[uuid.UUID] = Field(default_factory=list)
    tools: list[ToolBindingIn] = Field(default_factory=list)


class TeamUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    instructions: str | None = None
    mode: Literal["route", "coordinate"] | None = None
    model_id: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    member_config_ids: list[uuid.UUID] | None = None
    tools: list[ToolBindingIn] | None = None


class TeamMemberOut(BaseModel):
    agent_config_id: uuid.UUID
    agent_version_id: uuid.UUID
    position: int
    name: str
    slug: str
    version: int
    status: str


class TeamVersionOut(BaseModel):
    id: uuid.UUID
    version: int
    status: str
    instructions: str
    mode: str
    model_id: str
    temperature: float
    members: list[TeamMemberOut] = Field(default_factory=list)
    created_at: datetime


class TeamVersionSummaryOut(BaseModel):
    id: uuid.UUID
    version: int
    status: str
    mode: str
    member_count: int
    is_live: bool
    created_at: datetime


class TeamRestoreIn(BaseModel):
    """Restore a historical team version.

    ``as_draft=False`` (default) points live traffic at the selected snapshot.
    ``as_draft=True`` clones the snapshot into a new editable draft.
    """

    as_draft: bool = False


class TeamConfigOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str | None
    published_version_id: uuid.UUID | None
    updated_at: datetime
    tools: list[ToolBindingIn] = Field(default_factory=list)
    draft: TeamVersionOut | None = None
    published: TeamVersionOut | None = None


class WorkflowStepIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    target_type: Literal["agent", "team"]
    target_config_id: uuid.UUID
    condition_expression: str | None = Field(default=None, max_length=1000)


class WorkflowCreateIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    mode: Literal["sequential", "parallel"] = "sequential"
    steps: list[WorkflowStepIn] = Field(default_factory=list)


class WorkflowUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    mode: Literal["sequential", "parallel"] | None = None
    steps: list[WorkflowStepIn] | None = None


class WorkflowStepOut(BaseModel):
    id: uuid.UUID
    position: int
    name: str
    target_type: str
    target_config_id: uuid.UUID
    target_version_id: uuid.UUID
    target_name: str
    target_slug: str
    target_version: int
    target_status: str
    condition_expression: str | None = None


class WorkflowVersionOut(BaseModel):
    id: uuid.UUID
    version: int
    status: str
    mode: str
    steps: list[WorkflowStepOut] = Field(default_factory=list)
    created_at: datetime


class WorkflowVersionSummaryOut(BaseModel):
    id: uuid.UUID
    version: int
    status: str
    mode: str
    step_count: int
    is_live: bool
    created_at: datetime


class WorkflowRestoreIn(BaseModel):
    """Restore a historical workflow version.

    ``as_draft=False`` (default) points live traffic at the selected snapshot.
    ``as_draft=True`` clones the snapshot into a new editable draft.
    """

    as_draft: bool = False


class WorkflowConfigOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str | None
    published_version_id: uuid.UUID | None
    updated_at: datetime
    draft: WorkflowVersionOut | None = None
    published: WorkflowVersionOut | None = None


class WorkflowAssignmentsIn(BaseModel):
    user_ids: list[str] = Field(default_factory=list, max_length=500)

    @field_validator("user_ids")
    @classmethod
    def validate_user_ids(cls, values: list[str]) -> list[str]:
        normalized = sorted({value.strip() for value in values if value.strip()})
        if any(len(value) > 255 for value in normalized):
            raise ValueError("User IDs must be at most 255 characters")
        return normalized


class WorkflowAssignmentsOut(BaseModel):
    workflow_id: uuid.UUID
    user_ids: list[str] = Field(default_factory=list)


class TenantUserCreateIn(BaseModel):
    """Create by email; sign-in account + org mapping are provisioned automatically."""

    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    user_id: str | None = Field(
        default=None,
        max_length=255,
        description="Optional manual override (dev/auth-disabled only).",
    )
    role: Literal["tenant_admin", "end_user"] = "end_user"
    is_active: bool = True
    workflow_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    team_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)

    @field_validator("display_name")
    @classmethod
    def strip_display_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Value is required")
        return cleaned

    @field_validator("user_id")
    @classmethod
    def strip_user_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if "@" not in cleaned:
            raise ValueError("A valid email is required")
        return cleaned

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class TenantUserUpdateIn(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=40)
    role: Literal["tenant_admin", "end_user"] | None = None
    is_active: bool | None = None
    workflow_ids: list[uuid.UUID] | None = Field(default=None, max_length=500)
    team_ids: list[uuid.UUID] | None = Field(default=None, max_length=500)

    @field_validator("display_name")
    @classmethod
    def strip_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("display_name is required")
        return cleaned

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().lower() or None

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class TenantUserOut(BaseModel):
    id: uuid.UUID
    user_id: str
    display_name: str
    email: str | None
    phone: str | None = None
    role: str
    is_active: bool
    invite_pending: bool = False
    temporary_password: str | None = None
    sign_in_url: str | None = None
    workflow_ids: list[uuid.UUID] = Field(default_factory=list)
    team_ids: list[uuid.UUID] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class EndCustomerOut(BaseModel):
    """Verified public customer (OTP / inbound email), not Clerk staff."""

    id: uuid.UUID
    email: str
    display_name: str
    email_verified_at: datetime | None
    is_active: bool
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class EndCustomerUpdateIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("display_name")
    @classmethod
    def strip_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class KnowledgeBaseIn(BaseModel):
    name: str
    config: dict[str, Any] = Field(default_factory=dict)


class KnowledgeBaseUpdateIn(BaseModel):
    name: str | None = None
    config: dict[str, Any] | None = None


class KnowledgeBaseOut(BaseModel):
    id: uuid.UUID
    name: str
    config: dict[str, Any]


class KnowledgeSourceOut(BaseModel):
    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    kind: str
    uri: str
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class KnowledgeIngestUrlIn(BaseModel):
    url: str = Field(min_length=8, max_length=4000)


class KnowledgeIngestS3In(BaseModel):
    uri: str = Field(min_length=4, max_length=4000)


class KnowledgeIngestGithubIn(BaseModel):
    repo: str = Field(min_length=3, max_length=255, description="owner/name")
    path: str = Field(min_length=1, max_length=1000)
    ref: str = Field(default="main", min_length=1, max_length=255)
    credential_id: uuid.UUID | None = None


class ApprovalOut(BaseModel):
    id: uuid.UUID
    tool_name: str
    status: str
    redacted_arguments: dict[str, Any]
    resolved_by: str | None
    decision_reason: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    continuation_error: str | None = None
    expires_at: datetime | None = None
    created_at: datetime


class ApprovalResolveIn(BaseModel):
    approved: bool
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def rejection_requires_reason(self) -> "ApprovalResolveIn":
        if not self.approved and not (self.reason or "").strip():
            raise ValueError("A rejection reason is required")
        return self


TOOLKIT_CREDENTIAL_PROVIDERS = {
    credential.provider for spec in TOOLKIT_SPECS for credential in spec.credentials
}
CUSTOM_CREDENTIAL_PROVIDERS = {
    spec.credential_provider for spec in CUSTOM_TOOL_SPECS if spec.credential_provider
}
CREDENTIAL_PROVIDERS = {
    "openai",
    "anthropic",
    "groq",
    "moonshot",
    "nvidia",
    "gemini",
    "rest_api",
} | TOOLKIT_CREDENTIAL_PROVIDERS | CUSTOM_CREDENTIAL_PROVIDERS


class CredentialCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    provider: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=16_384)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        if value not in CREDENTIAL_PROVIDERS:
            raise ValueError("Unsupported credential provider")
        return value


class CredentialOut(BaseModel):
    id: uuid.UUID
    name: str
    provider: str
    key_version: str
    created_at: datetime


SERVICE_ACCOUNT_SCOPES = {
    "agent_os:admin",
    "teams:run",
    "workflows:run",
    "sessions:read",
    "sessions:delete",
    "traces:read",
    "mcp:access",
    "mcp:read",
    "mcp:run",
    "mcp:sessions:read",
    "service_accounts:read",
    "service_accounts:write",
    "service_accounts:delete",
}


class ServiceAccountCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    scopes: list[str] = Field(min_length=1, max_length=32)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_security_policy(self) -> "ServiceAccountCreateIn":
        invalid = set(self.scopes) - SERVICE_ACCOUNT_SCOPES
        if invalid:
            raise ValueError(f"Unsupported scopes: {', '.join(sorted(invalid))}")
        if len(self.scopes) != len(set(self.scopes)):
            raise ValueError("Scopes must be unique")
        if self.expires_at is not None:
            value = self.expires_at
            if value.tzinfo is None:
                raise ValueError("expires_at must include a timezone")
            if value <= datetime.now(value.tzinfo):
                raise ValueError("expires_at must be in the future")
        return self


class ServiceAccountOut(BaseModel):
    id: uuid.UUID
    name: str
    token_prefix: str
    scopes: list[str]
    created_by: str
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ServiceAccountCreatedOut(ServiceAccountOut):
    token: str


class TenantOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    branding: dict[str, Any]


class TraceSummaryOut(BaseModel):
    session_id: str
    agent_id: str
    spans: list[dict[str, Any]]
