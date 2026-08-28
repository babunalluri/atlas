from functools import lru_cache
from typing import Annotated

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Exact hostnames only (no wildcards). Groww hosts are included so local Stock
# Broker orgs can save Groww MCP and the Python groww_toolkit without a
# separate ops change.
DEFAULT_ALLOWED_OUTBOUND_HOSTS: frozenset[str] = frozenset(
    {"api.example.com", "httpbin.org", "mcp.groww.in", "api.groww.in"}
)
GROWW_MCP_HOST = "mcp.groww.in"
GROWW_API_HOST = "api.groww.in"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "Multi-Tenant Agent SaaS"
    environment: str = Field(default="development", validation_alias="ENVIRONMENT")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    # Runtime app role (must be NOSUPERUSER / NOBYPASSRLS so RLS applies).
    database_url: str = Field(
        default="postgresql+asyncpg://agent_saas_app:agent_saas_dev@localhost:5432/agent_saas",
        validation_alias=AliasChoices("DATABASE_URL", "BACKEND_DATABASE_URL"),
    )
    # Owner/superuser URL used only for Alembic + role bootstrap.
    # Empty → fall back to database_url (sqlite tests / single-role setups).
    migration_database_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "MIGRATION_DATABASE_URL",
            "DATABASE_MIGRATE_URL",
            "BACKEND_MIGRATION_DATABASE_URL",
        ),
    )
    database_app_password: SecretStr = Field(
        default=SecretStr("agent_saas_dev"),
        validation_alias=AliasChoices(
            "POSTGRES_APP_PASSWORD",
            "DATABASE_APP_PASSWORD",
        ),
    )
    agno_database_url: str = Field(
        default="postgresql+psycopg://agent_saas_app:agent_saas_dev@localhost:5432/agent_saas",
        validation_alias=AliasChoices("AGNO_DATABASE_URL", "BACKEND_AGNO_DATABASE_URL"),
    )
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default=["http://localhost:3000"],
        validation_alias=AliasChoices("CORS_ORIGINS", "BACKEND_CORS_ORIGINS"),
    )
    auth_issuer: str = Field(
        default="",
        validation_alias=AliasChoices(
            "AUTH_ISSUER",
            "BACKEND_AUTH_ISSUER",
        ),
        description="OIDC issuer (Keycloak realm URL).",
    )
    auth_audience: str | None = Field(
        default="atlas-web",
        validation_alias=AliasChoices(
            "AUTH_AUDIENCE",
            "BACKEND_AUTH_AUDIENCE",
        ),
    )
    auth_jwks_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AUTH_JWKS_URL",
            "BACKEND_AUTH_JWKS_URL",
        ),
    )
    auth_admin_secret: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices(
            "AUTH_ADMIN_SECRET",
            "BACKEND_AUTH_ADMIN_SECRET",
        ),
        description="Optional legacy IdP secret. Prefer KEYCLOAK_ADMIN_* settings.",
    )
    keycloak_admin_url: str = Field(
        default="",
        validation_alias=AliasChoices("KEYCLOAK_ADMIN_URL", "BACKEND_KEYCLOAK_ADMIN_URL"),
        description="Keycloak origin for Admin API (e.g. http://keycloak:8080).",
    )
    keycloak_admin_username: str = Field(
        default="admin",
        validation_alias=AliasChoices(
            "KEYCLOAK_ADMIN_USERNAME",
            "KEYCLOAK_ADMIN",
            "BACKEND_KEYCLOAK_ADMIN",
        ),
    )
    keycloak_admin_password: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices(
            "KEYCLOAK_ADMIN_PASSWORD",
            "BACKEND_KEYCLOAK_ADMIN_PASSWORD",
        ),
    )
    keycloak_admin_realm: str = Field(
        default="master",
        validation_alias=AliasChoices(
            "KEYCLOAK_ADMIN_REALM",
            "BACKEND_KEYCLOAK_ADMIN_REALM",
        ),
    )
    keycloak_realm: str = Field(
        default="",
        validation_alias=AliasChoices("KEYCLOAK_REALM", "BACKEND_KEYCLOAK_REALM"),
        description="Staff realm name. Empty derives from AUTH_ISSUER (atlas).",
    )
    keycloak_client_id: str = Field(
        default="atlas-web",
        validation_alias=AliasChoices(
            "KEYCLOAK_CLIENT_ID",
            "BACKEND_KEYCLOAK_CLIENT_ID",
        ),
        description="Confidential client used to verify the current password.",
    )
    keycloak_client_secret: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices(
            "KEYCLOAK_CLIENT_SECRET",
            "AUTH_KEYCLOAK_SECRET",
            "BACKEND_KEYCLOAK_CLIENT_SECRET",
        ),
    )
    identity_invite_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "IDENTITY_INVITE_ENABLED",
            "BACKEND_IDENTITY_INVITE_ENABLED",
        ),
        description="If true, Users create may fall back to pending-invite memberships.",
    )
    auth_provider: str = Field(
        default="oidc",
        validation_alias=AliasChoices("AUTH_PROVIDER", "BACKEND_AUTH_PROVIDER"),
        description="oidc (Keycloak/Zitadel/…). JWT verification uses JWKS.",
    )
    encryption_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices(
            "CREDENTIAL_ENCRYPTION_KEY",
            "BACKEND_ENCRYPTION_KEY",
        ),
    )
    encryption_key_version: str = Field(
        default="local-v1",
        validation_alias=AliasChoices(
            "ENCRYPTION_KEY_VERSION", "BACKEND_ENCRYPTION_KEY_VERSION"
        ),
    )
    # Comma-separated prior keys: "local-v0:secret0,local-v1:secret1"
    encryption_previous_keys_raw: str = Field(
        default="",
        validation_alias=AliasChoices(
            "ENCRYPTION_PREVIOUS_KEYS", "BACKEND_ENCRYPTION_PREVIOUS_KEYS"
        ),
    )
    aws_region: str = Field(default="us-east-1", validation_alias="AWS_REGION")
    aws_kms_key_id: str | None = Field(default=None, validation_alias="AWS_KMS_KEY_ID")
    aws_endpoint_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AWS_ENDPOINT_URL", "S3_ENDPOINT_URL"),
    )
    aws_access_key_id: str | None = Field(
        default=None, validation_alias="AWS_ACCESS_KEY_ID"
    )
    aws_secret_access_key: SecretStr | None = Field(
        default=None, validation_alias="AWS_SECRET_ACCESS_KEY"
    )
    redis_url: str = Field(
        default="memory://",
        validation_alias=AliasChoices("REDIS_URL", "BACKEND_REDIS_URL"),
        description="Redis URL for shared quotas/locks. Use memory:// to disable.",
    )
    document_bucket: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DOCUMENT_BUCKET", "BACKEND_DOCUMENT_BUCKET"),
    )
    db_pool_size: int = Field(default=5, ge=1, le=64, validation_alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(
        default=10, ge=0, le=128, validation_alias="DB_MAX_OVERFLOW"
    )
    allowed_outbound_hosts: Annotated[set[str], NoDecode] = Field(
        default_factory=lambda: set(DEFAULT_ALLOWED_OUTBOUND_HOSTS),
        validation_alias=AliasChoices("REST_TOOL_ALLOWED_HOSTS", "BACKEND_ALLOWED_OUTBOUND_HOSTS"),
        description="Exact HTTPS hostnames tools may call (REST_TOOL_ALLOWED_HOSTS).",
    )
    openai_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("OPENAI_API_KEY", "BACKEND_MODEL_API_KEY"),
    )
    anthropic_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="ANTHROPIC_API_KEY",
    )
    groq_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="GROQ_API_KEY",
    )
    moonshot_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("MOONSHOT_API_KEY", "KIMI_API_KEY"),
    )
    nvidia_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="NVIDIA_API_KEY",
    )
    gemini_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    )
    auth_disabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("AUTH_DISABLED", "BACKEND_AUTH_DISABLED"),
        description="Local-only auth bypass. Rejected when ENVIRONMENT is production.",
    )
    rate_limit_per_minute: int = Field(default=60, validation_alias="RATE_LIMIT_PER_MINUTE")
    public_chat_rate_limit_per_minute: int = Field(
        default=20,
        validation_alias="PUBLIC_CHAT_RATE_LIMIT_PER_MINUTE",
    )
    rate_limits_enabled: bool = Field(
        default=True,
        validation_alias="RATE_LIMITS_ENABLED",
        description="Admin API rate/concurrency middleware. Public chat uses its own limiter.",
    )
    tenant_concurrency_limit: int = Field(default=10, validation_alias="TENANT_CONCURRENCY_LIMIT")
    max_upload_bytes: int = Field(default=10_485_760, validation_alias="MAX_UPLOAD_BYTES")
    max_knowledge_chunks: int = Field(default=1000, validation_alias="MAX_KNOWLEDGE_CHUNKS")
    knowledge_top_k: int = Field(default=6, validation_alias="KNOWLEDGE_TOP_K")
    knowledge_score_threshold: float = Field(
        default=0.25, validation_alias="KNOWLEDGE_SCORE_THRESHOLD"
    )
    max_knowledge_context_chars: int = Field(
        default=12_000, validation_alias="MAX_KNOWLEDGE_CONTEXT_CHARS"
    )
    knowledge_chunk_size: int = Field(
        default=1200, ge=200, le=8000, validation_alias="KNOWLEDGE_CHUNK_SIZE"
    )
    knowledge_chunk_overlap: int = Field(
        default=150, ge=0, le=2000, validation_alias="KNOWLEDGE_CHUNK_OVERLAP"
    )
    knowledge_rrf_k: int = Field(
        default=60,
        ge=1,
        le=200,
        validation_alias="KNOWLEDGE_RRF_K",
        description="Reciprocal Rank Fusion constant for hybrid retrieval.",
    )
    knowledge_reranker: str = Field(
        default="local",
        validation_alias="KNOWLEDGE_RERANKER",
        description="Post-fusion reranker: local | cohere | off",
    )
    knowledge_cohere_rerank_model: str = Field(
        default="rerank-v3.5",
        validation_alias="KNOWLEDGE_COHERE_RERANK_MODEL",
    )
    cohere_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="COHERE_API_KEY",
        description="Optional. Used when KNOWLEDGE_RERANKER=cohere.",
    )
    embedding_model: str = Field(
        default="text-embedding-3-small", validation_alias="EMBEDDING_MODEL"
    )
    # Must match the pgvector column width (KnowledgeChunk.embedding Vector(N)).
    embedding_dimensions: int = Field(
        default=1536,
        ge=256,
        le=4096,
        validation_alias="EMBEDDING_DIMENSIONS",
    )
    document_upload_dir: str = Field(
        default="/data/uploads", validation_alias="DOCUMENT_UPLOAD_DIR"
    )
    agent_os_id: str = Field(default="multi-tenant-agent-saas", validation_alias="AGENT_OS_ID")
    scheduler_enabled: bool = Field(default=True, validation_alias="SCHEDULER_ENABLED")
    scheduler_poll_seconds: int = Field(
        default=15, ge=1, validation_alias="SCHEDULER_POLL_SECONDS"
    )
    signal_engine_ticker_enabled: bool = Field(
        default=False,
        validation_alias="SIGNAL_ENGINE_TICKER_ENABLED",
        description="Pre-compute signal snapshots for watched admin desks (~8 Hz when active).",
    )
    options_lab_ticker_enabled: bool = Field(
        default=False,
        validation_alias="OPTIONS_LAB_TICKER_ENABLED",
        description=(
            "Pre-compute Options Lab chain snapshots for watched desks (~8 Hz when active). "
            "Default off; enable in desk Compose / env."
        ),
    )
    param_chart_ticker_enabled: bool = Field(
        default=False,
        validation_alias="PARAM_CHART_TICKER_ENABLED",
        description=(
            "Paint Param Chart today-overlay from the ticker book and subscribe "
            "under/FUT/CE/PE as source=param_chart. Default off; enable in desk Compose."
        ),
    )
    options_lab_bots_enabled: bool = Field(
        default=False,
        validation_alias="OPTIONS_LAB_BOTS_ENABLED",
        description=(
            "Evaluate armed Options Lab paper bots ~once/minute. "
            "Live never auto-fires. Default off; enable in desk Compose / env."
        ),
    )
    kite_ticker_enabled: bool = Field(
        default=False,
        validation_alias="KITE_TICKER_ENABLED",
        description=(
            "Shared asyncio Kite WebSocket quote hub for Options Lab + Signal Engine. "
            "Default off; enable in desk Compose / env. One hub process per API key "
            "(Kite allows ~3 WS connections per key)."
        ),
    )
    scheduler_run_timeout_seconds: int = Field(
        default=900,
        ge=30,
        le=7200,
        validation_alias="SCHEDULER_RUN_TIMEOUT_SECONDS",
        description="Wall-clock cap per scheduled run so one hung target cannot stall the worker.",
    )
    sandbox_manager_url: str = Field(
        default="",
        validation_alias=AliasChoices("SANDBOX_MANAGER_URL", "BACKEND_SANDBOX_MANAGER_URL"),
    )
    sandbox_callback_base_url: str = Field(
        default="http://backend:7777",
        validation_alias=AliasChoices(
            "SANDBOX_CALLBACK_BASE_URL", "BACKEND_SANDBOX_CALLBACK_BASE_URL"
        ),
    )
    sandbox_instance_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "SANDBOX_INSTANCE_URL", "BACKEND_SANDBOX_INSTANCE_URL"
        ),
        description=(
            "Unique URL for this process (pod/task IP). Used so proxy callbacks with "
            "tenant credentials land on the owning replica. Falls back to "
            "SANDBOX_CALLBACK_BASE_URL when empty."
        ),
    )
    sandbox_python_image: str = Field(
        default="atlas-sandbox-python:local",
        validation_alias="SANDBOX_PYTHON_IMAGE",
    )
    sandbox_tenant_concurrency: int = Field(
        default=12, ge=1, le=32, validation_alias="SANDBOX_TENANT_CONCURRENCY"
    )
    sandbox_wall_seconds: int = Field(
        default=30, ge=5, le=120, validation_alias="SANDBOX_WALL_SECONDS"
    )
    sandbox_internal_token: SecretStr = Field(
        default=SecretStr("dev-sandbox-internal-token-change-me"),
        validation_alias=AliasChoices(
            "SANDBOX_INTERNAL_TOKEN", "BACKEND_SANDBOX_INTERNAL_TOKEN"
        ),
        description="Shared secret for sandbox-manager → backend proxy callbacks.",
    )
    agent_run_wall_seconds: int = Field(
        default=180,
        ge=30,
        le=900,
        validation_alias="AGENT_RUN_WALL_SECONDS",
        description="Wall-clock limit for a single agent/team/workflow SSE run.",
    )
    email_inbound_domain: str = Field(
        default="",
        validation_alias=AliasChoices("EMAIL_INBOUND_DOMAIN", "BACKEND_EMAIL_INBOUND_DOMAIN"),
        description="Resend receiving domain for team/workflow addresses.",
    )
    resend_webhook_secret: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices(
            "RESEND_WEBHOOK_SECRET", "BACKEND_RESEND_WEBHOOK_SECRET"
        ),
        description="Svix signing secret for Resend inbound webhooks.",
    )
    resend_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("RESEND_API_KEY", "BACKEND_RESEND_API_KEY"),
        description="Optional platform fallback when a tenant has no resend credential.",
    )
    app_public_url: str = Field(
        default="http://localhost:3000",
        validation_alias=AliasChoices("APP_PUBLIC_URL", "NEXT_PUBLIC_APP_URL"),
        description="Public web origin for approval links in email auto-replies.",
    )
    dev_user_password: SecretStr = Field(
        default=SecretStr("atlas-dev-password"),
        validation_alias=AliasChoices("DEV_USER_PASSWORD", "BACKEND_DEV_USER_PASSWORD"),
        description=(
            "Development-only password applied to invited users so email OTP "
            "can be skipped via password sign-in."
        ),
    )
    public_email_run_wall_seconds: int = Field(
        default=55,
        ge=10,
        le=120,
        validation_alias="PUBLIC_EMAIL_RUN_WALL_SECONDS",
        description="Wall-clock limit for inbound email → team/workflow runs.",
    )
    billing_provider: str = Field(
        default="dummy",
        validation_alias=AliasChoices("BILLING_PROVIDER", "BACKEND_BILLING_PROVIDER"),
        description="Billing checkout provider: dummy (instant), razorpay, or stripe.",
    )
    billing_currency: str = Field(
        default="INR",
        validation_alias=AliasChoices("BILLING_CURRENCY", "BACKEND_BILLING_CURRENCY"),
        description="ISO currency for Razorpay amounts (INR uses paise).",
    )
    razorpay_key_id: str = Field(
        default="",
        validation_alias=AliasChoices("RAZORPAY_KEY_ID", "BACKEND_RAZORPAY_KEY_ID"),
    )
    razorpay_key_secret: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices(
            "RAZORPAY_KEY_SECRET", "BACKEND_RAZORPAY_KEY_SECRET"
        ),
    )
    razorpay_webhook_secret: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices(
            "RAZORPAY_WEBHOOK_SECRET", "BACKEND_RAZORPAY_WEBHOOK_SECRET"
        ),
    )
    stripe_secret_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("STRIPE_SECRET_KEY", "BACKEND_STRIPE_SECRET_KEY"),
    )
    stripe_webhook_secret: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices(
            "STRIPE_WEBHOOK_SECRET", "BACKEND_STRIPE_WEBHOOK_SECRET"
        ),
    )

    @field_validator("document_bucket", "aws_endpoint_url", "aws_access_key_id", mode="before")
    @classmethod
    def empty_str_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("database_url")
    @classmethod
    def require_async_driver(cls, value: str) -> str:
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("allowed_outbound_hosts", mode="before")
    @classmethod
    def split_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            return {
                item.strip().lower().rstrip(".")
                for item in value.split(",")
                if item.strip()
            }
        return value

    @property
    def encryption_previous_keys(self) -> dict[str, str]:
        raw = self.encryption_previous_keys_raw.strip()
        if not raw:
            return {}
        out: dict[str, str] = {}
        for part in raw.split(","):
            version, sep, secret = part.partition(":")
            if sep and version.strip() and secret:
                out[version.strip()] = secret
        return out

    @property
    def effective_jwks_url(self) -> str:
        if self.auth_jwks_url:
            return self.auth_jwks_url
        if not self.auth_issuer:
            return ""
        issuer = self.auth_issuer.rstrip("/")
        # Keycloak (and compatible realm IdPs) serve JWKS at the OIDC certs path.
        if "/realms/" in issuer:
            return f"{issuer}/protocol/openid-connect/certs"
        # Non-Keycloak OIDC: set AUTH_JWKS_URL explicitly (from discovery jwks_uri).
        return ""

    @property
    def is_development(self) -> bool:
        return self.environment.lower() in {"development", "dev", "test", "local"}

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod", "staging"}

    @property
    def effective_migration_database_url(self) -> str:
        return self.migration_database_url or self.database_url

    @model_validator(mode="after")
    def include_local_groww_mcp_host(self) -> "Settings":
        hosts = {host.lower().rstrip(".") for host in self.allowed_outbound_hosts}
        # Env replaces the Python default; keep Groww hosts on local stacks so
        # Stock Broker orgs can save MCP / groww_toolkit without editing
        # REST_TOOL_ALLOWED_HOSTS.
        if self.is_development:
            hosts.add(GROWW_MCP_HOST)
            hosts.add(GROWW_API_HOST)
        self.allowed_outbound_hosts = hosts
        return self

    @model_validator(mode="after")
    def reject_auth_bypass_outside_dev(self) -> "Settings":
        if self.auth_disabled and self.is_production:
            raise ValueError(
                "AUTH_DISABLED=true is not allowed when ENVIRONMENT is "
                f"{self.environment!r}"
            )
        if self.auth_disabled and not self.is_development:
            raise ValueError(
                "AUTH_DISABLED=true is only allowed for development/dev/test/local"
            )
        if self.is_production:
            token = self.sandbox_internal_token.get_secret_value()
            if not token or token == "dev-sandbox-internal-token-change-me":
                raise ValueError(
                    "SANDBOX_INTERNAL_TOKEN must be set to a non-default secret "
                    "when ENVIRONMENT is production/staging"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
