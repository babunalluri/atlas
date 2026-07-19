import enum
import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class Role(enum.StrEnum):
    platform_admin = "platform_admin"
    tenant_admin = "tenant_admin"
    end_user = "end_user"


class AgentStatus(enum.StrEnum):
    draft = "draft"
    published = "published"
    archived = "archived"


class ApprovalStatus(enum.StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    executed = "executed"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TenantScoped:
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    clerk_org_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    branding: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class TenantMcpSettings(Base, TenantScoped, TimestampMixin):
    __tablename__ = "tenant_mcp_settings"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_tenant_mcp_settings_tenant"),
    )


class Membership(Base, TenantScoped, TimestampMixin):
    __tablename__ = "memberships"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role), nullable=False)
    __table_args__ = (Index("uq_membership_tenant_user", "tenant_id", "user_id", unique=True),)


class AgentConfig(Base, TenantScoped, TimestampMixin):
    __tablename__ = "agent_configs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    published_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    __table_args__ = (
        Index("uq_agent_tenant_slug", "tenant_id", "slug", unique=True),
        UniqueConstraint("tenant_id", "id", name="uq_agent_config_tenant_id"),
    )


class AgentVersion(Base, TenantScoped, TimestampMixin):
    __tablename__ = "agent_versions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    agent_config_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agent_configs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[AgentStatus] = mapped_column(Enum(AgentStatus), default=AgentStatus.draft)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    temperature: Mapped[float] = mapped_column(Float, default=0.2, nullable=False)
    memory_mode: Mapped[str] = mapped_column(String(32), default="session", nullable=False)
    team_config: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    __table_args__ = (
        Index("uq_agent_version", "tenant_id", "agent_config_id", "version", unique=True),
        UniqueConstraint(
            "tenant_id", "agent_config_id", "id", name="uq_agent_version_tenant_config_id"
        ),
    )


class TeamConfig(Base, TenantScoped, TimestampMixin):
    __tablename__ = "team_configs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    published_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    __table_args__ = (
        Index("uq_team_tenant_slug", "tenant_id", "slug", unique=True),
        UniqueConstraint("tenant_id", "id", name="uq_team_config_tenant_id"),
    )


class TeamVersion(Base, TenantScoped, TimestampMixin):
    __tablename__ = "team_versions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    team_config_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[AgentStatus] = mapped_column(Enum(AgentStatus), default=AgentStatus.draft)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(String(32), default="coordinate", nullable=False)
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    temperature: Mapped[float] = mapped_column(Float, default=0.2, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "team_config_id"],
            ["team_configs.tenant_id", "team_configs.id"],
            ondelete="CASCADE",
            name="fk_team_version_tenant_config",
        ),
        Index("uq_team_version", "tenant_id", "team_config_id", "version", unique=True),
        UniqueConstraint(
            "tenant_id", "team_config_id", "id", name="uq_team_version_tenant_config_id"
        ),
    )


class TeamMember(Base, TenantScoped, TimestampMixin):
    __tablename__ = "team_members"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    team_config_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    team_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    agent_config_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    agent_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "team_config_id", "team_version_id"],
            ["team_versions.tenant_id", "team_versions.team_config_id", "team_versions.id"],
            ondelete="CASCADE",
            name="fk_team_member_tenant_team_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "agent_config_id", "agent_version_id"],
            ["agent_versions.tenant_id", "agent_versions.agent_config_id", "agent_versions.id"],
            ondelete="RESTRICT",
            name="fk_team_member_tenant_agent_version",
        ),
        Index("uq_team_member_position", "team_version_id", "position", unique=True),
        Index("ix_team_members_tenant_agent", "tenant_id", "agent_config_id"),
    )


class WorkflowConfig(Base, TenantScoped, TimestampMixin):
    __tablename__ = "workflow_configs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    published_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    __table_args__ = (
        Index("uq_workflow_tenant_slug", "tenant_id", "slug", unique=True),
        UniqueConstraint("tenant_id", "id", name="uq_workflow_config_tenant_id"),
    )


class WorkflowAssignment(Base, TenantScoped, TimestampMixin):
    """Grants one tenant user access to a published workflow."""

    __tablename__ = "workflow_assignments"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workflow_config_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    assigned_by: Mapped[str] = mapped_column(String(255), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workflow_config_id"],
            ["workflow_configs.tenant_id", "workflow_configs.id"],
            ondelete="CASCADE",
            name="fk_workflow_assignment_tenant_config",
        ),
        UniqueConstraint(
            "tenant_id",
            "workflow_config_id",
            "user_id",
            name="uq_workflow_assignment_tenant_workflow_user",
        ),
    )


class WorkflowVersion(Base, TenantScoped, TimestampMixin):
    __tablename__ = "workflow_versions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workflow_config_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[AgentStatus] = mapped_column(Enum(AgentStatus), default=AgentStatus.draft)
    mode: Mapped[str] = mapped_column(String(32), default="sequential", nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workflow_config_id"],
            ["workflow_configs.tenant_id", "workflow_configs.id"],
            ondelete="CASCADE",
            name="fk_workflow_version_tenant_config",
        ),
        Index(
            "uq_workflow_version",
            "tenant_id",
            "workflow_config_id",
            "version",
            unique=True,
        ),
        UniqueConstraint(
            "tenant_id",
            "workflow_config_id",
            "id",
            name="uq_workflow_version_tenant_config_id",
        ),
    )


class WorkflowStep(Base, TenantScoped, TimestampMixin):
    __tablename__ = "workflow_steps"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workflow_config_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    workflow_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    agent_config_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    team_config_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    team_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    condition_expression: Mapped[str | None] = mapped_column(String(1000))
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workflow_config_id", "workflow_version_id"],
            [
                "workflow_versions.tenant_id",
                "workflow_versions.workflow_config_id",
                "workflow_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_workflow_step_tenant_workflow_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "agent_config_id", "agent_version_id"],
            ["agent_versions.tenant_id", "agent_versions.agent_config_id", "agent_versions.id"],
            ondelete="RESTRICT",
            name="fk_workflow_step_tenant_agent_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "team_config_id", "team_version_id"],
            ["team_versions.tenant_id", "team_versions.team_config_id", "team_versions.id"],
            ondelete="RESTRICT",
            name="fk_workflow_step_tenant_team_version",
        ),
        CheckConstraint(
            "(target_type = 'agent' AND agent_config_id IS NOT NULL AND "
            "agent_version_id IS NOT NULL AND team_config_id IS NULL AND team_version_id IS NULL) "
            "OR (target_type = 'team' AND team_config_id IS NOT NULL AND "
            "team_version_id IS NOT NULL AND agent_config_id IS NULL AND agent_version_id IS NULL)",
            name="ck_workflow_step_target",
        ),
        Index("uq_workflow_step_position", "workflow_version_id", "position", unique=True),
    )


class AgentToolBinding(Base, TenantScoped, TimestampMixin):
    __tablename__ = "agent_tool_bindings"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    agent_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agent_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tool_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tool_definition_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    credential_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "tool_definition_id"],
            ["tool_definitions.tenant_id", "tool_definitions.id"],
            ondelete="RESTRICT",
            name="fk_tool_binding_tenant_definition",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "credential_id"],
            ["tenant_credentials.tenant_id", "tenant_credentials.id"],
            ondelete="SET NULL",
            name="fk_tool_binding_tenant_credential",
        ),
        CheckConstraint(
            "(tool_key IS NOT NULL) <> (tool_definition_id IS NOT NULL)",
            name="ck_tool_binding_source",
        ),
        Index("ix_agent_tool_bindings_tenant_definition", "tenant_id", "tool_definition_id"),
    )


class ToolDefinition(Base, TenantScoped, TimestampMixin):
    __tablename__ = "tool_definitions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    http_method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    path: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    response_description: Mapped[str | None] = mapped_column(Text)
    response_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    headers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    credential_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    connection_status: Mapped[str] = mapped_column(
        String(32), default="unvalidated", nullable=False
    )
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_validation_error: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = (
        Index("uq_tool_definition_tenant_slug", "tenant_id", "slug", unique=True),
        Index("ix_tool_definitions_tenant_kind_active", "tenant_id", "kind", "active"),
        UniqueConstraint("tenant_id", "id", name="uq_tool_definition_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "credential_id"],
            ["tenant_credentials.tenant_id", "tenant_credentials.id"],
            ondelete="SET NULL",
            name="fk_tool_definition_tenant_credential",
        ),
    )


class KnowledgeBase(Base, TenantScoped, TimestampMixin):
    __tablename__ = "knowledge_bases"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    __table_args__ = (UniqueConstraint("tenant_id", "id", name="uq_knowledge_base_tenant_id"),)


class KnowledgeSource(Base, TenantScoped, TimestampMixin):
    __tablename__ = "knowledge_sources"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    embedding_model: Mapped[str | None] = mapped_column(String(255))
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(1000))
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            ondelete="CASCADE",
            name="fk_knowledge_source_tenant_base",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_knowledge_source_tenant_id"),
        Index(
            "uq_knowledge_source_content",
            "tenant_id",
            "knowledge_base_id",
            "content_hash",
            unique=True,
        ),
    )


class KnowledgeChunk(Base, TenantScoped, TimestampMixin):
    __tablename__ = "knowledge_chunks"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            ondelete="CASCADE",
            name="fk_knowledge_chunk_tenant_base",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["knowledge_sources.tenant_id", "knowledge_sources.id"],
            ondelete="CASCADE",
            name="fk_knowledge_chunk_tenant_source",
        ),
        Index(
            "uq_knowledge_chunk_content",
            "tenant_id",
            "source_id",
            "content_hash",
            unique=True,
        ),
        Index(
            "ix_knowledge_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class TenantCredential(Base, TenantScoped, TimestampMixin):
    __tablename__ = "tenant_credentials"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)
    key_version: Mapped[str] = mapped_column(String(32), default="local-v1", nullable=False)
    __table_args__ = (UniqueConstraint("tenant_id", "id", name="uq_tenant_credential_tenant_id"),)


class ServiceAccount(Base, TenantScoped, TimestampMixin):
    __tablename__ = "service_accounts"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(24), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index("ix_service_accounts_tenant_active", "tenant_id", "revoked_at"),
        UniqueConstraint("tenant_id", "id", name="uq_service_account_tenant_id"),
    )


class ConversationSession(Base, TenantScoped, TimestampMixin):
    __tablename__ = "conversation_sessions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    external_session_id: Mapped[str] = mapped_column(String(255), nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), default="agent", nullable=False)
    agent_config_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    team_config_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    team_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    workflow_config_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    workflow_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(255))
    runtime_session_id: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    runtime_user_id: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    last_run_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_conversation_session_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "agent_config_id", "agent_version_id"],
            ["agent_versions.tenant_id", "agent_versions.agent_config_id", "agent_versions.id"],
            ondelete="RESTRICT",
            name="fk_conversation_agent_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "team_config_id", "team_version_id"],
            ["team_versions.tenant_id", "team_versions.team_config_id", "team_versions.id"],
            ondelete="RESTRICT",
            name="fk_conversation_team_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workflow_config_id", "workflow_version_id"],
            [
                "workflow_versions.tenant_id",
                "workflow_versions.workflow_config_id",
                "workflow_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_conversation_workflow_version",
        ),
        Index(
            "uq_conversation_session_tenant_external",
            "tenant_id",
            "external_session_id",
            unique=True,
        ),
        CheckConstraint(
            "(target_type = 'agent' AND agent_config_id IS NOT NULL AND "
            "agent_version_id IS NOT NULL AND team_config_id IS NULL AND team_version_id IS NULL "
            "AND workflow_config_id IS NULL AND workflow_version_id IS NULL) "
            "OR (target_type = 'team' AND team_config_id IS NOT NULL AND "
            "team_version_id IS NOT NULL AND agent_config_id IS NULL AND agent_version_id IS NULL "
            "AND workflow_config_id IS NULL AND workflow_version_id IS NULL) "
            "OR (target_type = 'workflow' AND workflow_config_id IS NOT NULL AND "
            "workflow_version_id IS NOT NULL AND agent_config_id IS NULL "
            "AND agent_version_id IS NULL "
            "AND team_config_id IS NULL AND team_version_id IS NULL)",
            name="ck_conversation_session_target",
        ),
    )


class ApprovalBinding(Base, TenantScoped, TimestampMixin):
    __tablename__ = "approval_bindings"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    redacted_arguments: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus), default=ApprovalStatus.pending, nullable=False
    )
    resolved_by: Mapped[str | None] = mapped_column(String(255))
    run_id: Mapped[str] = mapped_column(
        String(255), default=lambda: f"legacy:{uuid.uuid4()}", nullable=False, index=True
    )
    requirement_id: Mapped[str] = mapped_column(
        String(255), default=lambda: f"legacy:{uuid.uuid4()}", nullable=False
    )
    requirement: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    decision_reason: Mapped[str | None] = mapped_column(String(1000))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    continued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    continuation_error: Mapped[str | None] = mapped_column(String(1000))
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["conversation_sessions.tenant_id", "conversation_sessions.id"],
            ondelete="CASCADE",
            name="fk_approval_tenant_session",
        ),
        Index("uq_approval_requirement", "tenant_id", "run_id", "requirement_id", unique=True),
    )


class TraceRecord(Base, TenantScoped):
    __tablename__ = "trace_records"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[str | None] = mapped_column(String(255), index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    external_session_id: Mapped[str] = mapped_column(String(255), nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
    input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_trace_record_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["conversation_sessions.tenant_id", "conversation_sessions.id"],
            ondelete="CASCADE",
            name="fk_trace_record_tenant_session",
        ),
        Index("ix_trace_records_tenant_started", "tenant_id", "started_at"),
        Index("ix_trace_records_tenant_status", "tenant_id", "status"),
    )


class TraceSpan(Base, TenantScoped):
    __tablename__ = "trace_spans"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    parent_span_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(String(2000))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "trace_id"],
            ["trace_records.tenant_id", "trace_records.id"],
            ondelete="CASCADE",
            name="fk_trace_span_tenant_trace",
        ),
        Index("ix_trace_spans_trace_sequence", "tenant_id", "trace_id", "sequence"),
    )


class EvalDefinition(Base, TenantScoped, TimestampMixin):
    __tablename__ = "eval_definitions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    suite: Mapped[str] = mapped_column(String(32), default="smoke", nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    agent_config_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    team_config_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    team_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    workflow_config_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    workflow_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    cases: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    pass_threshold: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    run_on_publish: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_eval_definition_tenant_id"),
        UniqueConstraint("tenant_id", "slug", name="uq_eval_definition_tenant_slug"),
        ForeignKeyConstraint(
            ["tenant_id", "agent_config_id", "agent_version_id"],
            ["agent_versions.tenant_id", "agent_versions.agent_config_id", "agent_versions.id"],
            ondelete="CASCADE",
            name="fk_eval_definition_agent_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "team_config_id", "team_version_id"],
            ["team_versions.tenant_id", "team_versions.team_config_id", "team_versions.id"],
            ondelete="CASCADE",
            name="fk_eval_definition_team_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workflow_config_id", "workflow_version_id"],
            [
                "workflow_versions.tenant_id",
                "workflow_versions.workflow_config_id",
                "workflow_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_eval_definition_workflow_version",
        ),
        CheckConstraint(
            "(target_type = 'agent' AND agent_config_id IS NOT NULL AND "
            "agent_version_id IS NOT NULL AND team_config_id IS NULL AND "
            "team_version_id IS NULL AND workflow_config_id IS NULL AND "
            "workflow_version_id IS NULL) OR "
            "(target_type = 'team' AND team_config_id IS NOT NULL AND "
            "team_version_id IS NOT NULL AND agent_config_id IS NULL AND "
            "agent_version_id IS NULL AND workflow_config_id IS NULL AND "
            "workflow_version_id IS NULL) OR "
            "(target_type = 'workflow' AND workflow_config_id IS NOT NULL AND "
            "workflow_version_id IS NOT NULL AND agent_config_id IS NULL AND "
            "agent_version_id IS NULL AND team_config_id IS NULL AND team_version_id IS NULL)",
            name="ck_eval_definition_target",
        ),
        CheckConstraint(
            "pass_threshold >= 0 AND pass_threshold <= 1",
            name="ck_eval_definition_threshold",
        ),
        Index("ix_eval_definitions_tenant_suite", "tenant_id", "suite"),
    )

    @property
    def target_id(self) -> uuid.UUID:
        value = self.agent_config_id or self.team_config_id or self.workflow_config_id
        assert value is not None
        return value

    @property
    def version_id(self) -> uuid.UUID:
        value = self.agent_version_id or self.team_version_id or self.workflow_version_id
        assert value is not None
        return value


class EvalRun(Base, TenantScoped):
    __tablename__ = "eval_runs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    eval_definition_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    passed: Mapped[bool | None] = mapped_column(Boolean)
    total_cases: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    passed_cases: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(String(2000))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_eval_run_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "eval_definition_id"],
            ["eval_definitions.tenant_id", "eval_definitions.id"],
            ondelete="CASCADE",
            name="fk_eval_run_tenant_definition",
        ),
        Index("ix_eval_runs_tenant_started", "tenant_id", "started_at"),
    )


class EvalCaseResult(Base, TenantScoped):
    __tablename__ = "eval_case_results"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    eval_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    case_key: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    input: Mapped[str] = mapped_column(Text, nullable=False)
    expected_output: Mapped[str] = mapped_column(Text, nullable=False)
    actual_output: Mapped[str | None] = mapped_column(Text)
    evaluator: Mapped[str] = mapped_column(String(32), default="contains", nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(String(2000))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "eval_run_id"],
            ["eval_runs.tenant_id", "eval_runs.id"],
            ondelete="CASCADE",
            name="fk_eval_case_result_tenant_run",
        ),
        UniqueConstraint(
            "tenant_id", "eval_run_id", "case_key", name="uq_eval_case_result_run_key"
        ),
    )


class MetricDailyAggregate(Base, TenantScoped, TimestampMixin):
    __tablename__ = "metric_daily_aggregates"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    metric_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    run_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    paused_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_p50_ms: Mapped[int | None] = mapped_column(Integer)
    latency_p95_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    approval_waits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unique_sessions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    top_tools: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "metric_date",
            "target_type",
            "target_id",
            name="uq_metric_daily_dimension",
        ),
        Index("ix_metric_daily_tenant_date", "tenant_id", "metric_date"),
    )


class Schedule(Base, TenantScoped, TimestampMixin):
    __tablename__ = "schedules"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(255), nullable=False)
    timezone: Mapped[str] = mapped_column(String(100), default="UTC", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    agent_config_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    team_config_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    team_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    workflow_config_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    workflow_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_status: Mapped[str | None] = mapped_column(String(32))
    last_error: Mapped[str | None] = mapped_column(String(2000))
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_schedule_tenant_id"),
        UniqueConstraint("tenant_id", "name", name="uq_schedule_tenant_name"),
        ForeignKeyConstraint(
            ["tenant_id", "agent_config_id", "agent_version_id"],
            ["agent_versions.tenant_id", "agent_versions.agent_config_id", "agent_versions.id"],
            ondelete="RESTRICT",
            name="fk_schedule_agent_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "team_config_id", "team_version_id"],
            ["team_versions.tenant_id", "team_versions.team_config_id", "team_versions.id"],
            ondelete="RESTRICT",
            name="fk_schedule_team_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workflow_config_id", "workflow_version_id"],
            [
                "workflow_versions.tenant_id",
                "workflow_versions.workflow_config_id",
                "workflow_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_schedule_workflow_version",
        ),
        CheckConstraint(
            "(target_type = 'agent' AND agent_config_id IS NOT NULL AND "
            "agent_version_id IS NOT NULL AND team_config_id IS NULL AND "
            "team_version_id IS NULL AND workflow_config_id IS NULL AND "
            "workflow_version_id IS NULL) OR "
            "(target_type = 'team' AND team_config_id IS NOT NULL AND "
            "team_version_id IS NOT NULL AND agent_config_id IS NULL AND "
            "agent_version_id IS NULL AND workflow_config_id IS NULL AND "
            "workflow_version_id IS NULL) OR "
            "(target_type = 'workflow' AND workflow_config_id IS NOT NULL AND "
            "workflow_version_id IS NOT NULL AND agent_config_id IS NULL AND "
            "agent_version_id IS NULL AND team_config_id IS NULL AND team_version_id IS NULL)",
            name="ck_schedule_target",
        ),
        Index("ix_schedules_tenant_due", "tenant_id", "enabled", "next_run_at"),
    )

    @property
    def target_id(self) -> uuid.UUID:
        value = self.agent_config_id or self.team_config_id or self.workflow_config_id
        assert value is not None
        return value

    @property
    def version_id(self) -> uuid.UUID:
        value = self.agent_version_id or self.team_version_id or self.workflow_version_id
        assert value is not None
        return value


class ScheduleRun(Base, TenantScoped):
    __tablename__ = "schedule_runs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    schedule_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
    session_id: Mapped[str] = mapped_column(String(255), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(255))
    output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(String(2000))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_schedule_run_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "schedule_id"],
            ["schedules.tenant_id", "schedules.id"],
            ondelete="CASCADE",
            name="fk_schedule_run_tenant_schedule",
        ),
        Index("ix_schedule_runs_tenant_started", "tenant_id", "started_at"),
    )


class AuditEvent(Base, TenantScoped):
    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(255))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PlatformAuditEvent(Base):
    """Audit trail for platform-wide actions that cannot belong to one RLS scope."""

    __tablename__ = "platform_audit_events"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="SET NULL"), index=True
    )
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
