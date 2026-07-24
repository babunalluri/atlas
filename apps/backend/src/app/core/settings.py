from functools import lru_cache
from typing import Annotated

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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
    database_url: str = Field(
        default="postgresql+asyncpg://agent_saas:agent_saas_dev@localhost:5432/agent_saas",
        validation_alias=AliasChoices("DATABASE_URL", "BACKEND_DATABASE_URL"),
    )
    agno_database_url: str = Field(
        default="postgresql+psycopg://agent_saas:agent_saas_dev@localhost:5432/agent_saas",
        validation_alias=AliasChoices("AGNO_DATABASE_URL", "BACKEND_AGNO_DATABASE_URL"),
    )
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default=["http://localhost:3000"],
        validation_alias=AliasChoices("CORS_ORIGINS", "BACKEND_CORS_ORIGINS"),
    )
    clerk_issuer: str = Field(
        default="", validation_alias=AliasChoices("CLERK_ISSUER", "BACKEND_CLERK_ISSUER")
    )
    clerk_audience: str | None = Field(
        default="agent-saas",
        validation_alias=AliasChoices("CLERK_AUDIENCE", "BACKEND_CLERK_AUDIENCE"),
    )
    clerk_jwks_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CLERK_JWKS_URL", "BACKEND_CLERK_JWKS_URL"),
    )
    encryption_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices(
            "CREDENTIAL_ENCRYPTION_KEY",
            "BACKEND_ENCRYPTION_KEY",
        ),
    )
    aws_region: str = Field(default="us-east-1", validation_alias="AWS_REGION")
    aws_kms_key_id: str | None = Field(default=None, validation_alias="AWS_KMS_KEY_ID")
    allowed_outbound_hosts: Annotated[set[str], NoDecode] = Field(
        default_factory=lambda: {"api.example.com", "httpbin.org"},
        validation_alias=AliasChoices("REST_TOOL_ALLOWED_HOSTS", "BACKEND_ALLOWED_OUTBOUND_HOSTS"),
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
    auth_disabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("AUTH_DISABLED", "BACKEND_AUTH_DISABLED"),
    )
    rate_limit_per_minute: int = Field(default=60, validation_alias="RATE_LIMIT_PER_MINUTE")
    public_chat_rate_limit_per_minute: int = Field(
        default=20,
        validation_alias="PUBLIC_CHAT_RATE_LIMIT_PER_MINUTE",
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
    embedding_model: str = Field(
        default="text-embedding-3-small", validation_alias="EMBEDDING_MODEL"
    )
    document_upload_dir: str = Field(
        default="/data/uploads", validation_alias="DOCUMENT_UPLOAD_DIR"
    )
    agent_os_id: str = Field(default="multi-tenant-agent-saas", validation_alias="AGENT_OS_ID")
    scheduler_enabled: bool = Field(default=True, validation_alias="SCHEDULER_ENABLED")
    scheduler_poll_seconds: int = Field(
        default=15, ge=1, validation_alias="SCHEDULER_POLL_SECONDS"
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
    sandbox_python_image: str = Field(
        default="atlas-sandbox-python:local",
        validation_alias="SANDBOX_PYTHON_IMAGE",
    )
    sandbox_tenant_concurrency: int = Field(
        default=4, ge=1, le=32, validation_alias="SANDBOX_TENANT_CONCURRENCY"
    )
    sandbox_wall_seconds: int = Field(
        default=30, ge=5, le=120, validation_alias="SANDBOX_WALL_SECONDS"
    )

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
            return {item.strip().lower() for item in value.split(",") if item.strip()}
        return value

    @property
    def effective_jwks_url(self) -> str:
        if self.clerk_jwks_url:
            return self.clerk_jwks_url
        if not self.clerk_issuer:
            return ""
        return f"{self.clerk_issuer.rstrip('/')}/.well-known/jwks.json"

    @property
    def is_development(self) -> bool:
        return self.environment.lower() in {"development", "dev", "test", "local"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
