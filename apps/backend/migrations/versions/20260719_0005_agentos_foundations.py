"""durable sessions, HITL bridge, and indexed knowledge

Revision ID: 20260719_0005
Revises: 20260719_0004
Create Date: 2026-07-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0005"
down_revision: Union[str, None] = "20260719_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Knowledge indexing state and safe deduplication/backfill.
    op.add_column("knowledge_sources", sa.Column("content_hash", sa.String(64)))
    op.add_column("knowledge_sources", sa.Column("embedding_model", sa.String(255)))
    op.add_column(
        "knowledge_sources",
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("knowledge_sources", sa.Column("error_message", sa.String(1000)))
    op.add_column("knowledge_chunks", sa.Column("content_hash", sa.String(64)))
    op.add_column(
        "knowledge_chunks",
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        "UPDATE knowledge_sources SET content_hash = encode(digest(id::text, 'sha256'), 'hex') "
        "WHERE content_hash IS NULL"
    )
    op.execute(
        "UPDATE knowledge_chunks SET content_hash = encode(digest(content, 'sha256'), 'hex'), "
        "token_count = GREATEST(1, length(content) / 4) WHERE content_hash IS NULL"
    )
    op.execute(
        "UPDATE knowledge_sources SET status = 'failed', "
        "error_message = 'Legacy source requires reindexing to generate embeddings' "
        "WHERE status = 'ready' AND NOT EXISTS ("
        "SELECT 1 FROM knowledge_chunks kc WHERE kc.source_id = knowledge_sources.id "
        "AND kc.embedding IS NOT NULL)"
    )
    op.create_unique_constraint(
        "uq_knowledge_base_tenant_id", "knowledge_bases", ["tenant_id", "id"]
    )
    op.create_unique_constraint(
        "uq_knowledge_source_tenant_id", "knowledge_sources", ["tenant_id", "id"]
    )
    op.create_foreign_key(
        "fk_knowledge_source_tenant_base",
        "knowledge_sources",
        "knowledge_bases",
        ["tenant_id", "knowledge_base_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_knowledge_chunk_tenant_base",
        "knowledge_chunks",
        "knowledge_bases",
        ["tenant_id", "knowledge_base_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_knowledge_chunk_tenant_source",
        "knowledge_chunks",
        "knowledge_sources",
        ["tenant_id", "source_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "uq_knowledge_source_content",
        "knowledge_sources",
        ["tenant_id", "knowledge_base_id", "content_hash"],
        unique=True,
    )
    op.create_index(
        "uq_knowledge_chunk_content",
        "knowledge_chunks",
        ["tenant_id", "source_id", "content_hash"],
        unique=True,
    )

    # Product-owned session pins map opaque customer IDs to native AgentOS IDs.
    op.drop_constraint(
        "conversation_sessions_external_session_id_key",
        "conversation_sessions",
        type_="unique",
    )
    op.add_column(
        "conversation_sessions",
        sa.Column("target_type", sa.String(16), nullable=False, server_default="agent"),
    )
    op.add_column("conversation_sessions", sa.Column("team_config_id", sa.Uuid()))
    op.add_column("conversation_sessions", sa.Column("team_version_id", sa.Uuid()))
    op.add_column("conversation_sessions", sa.Column("runtime_session_id", sa.String(512)))
    op.add_column("conversation_sessions", sa.Column("runtime_user_id", sa.String(512)))
    op.add_column("conversation_sessions", sa.Column("last_run_id", sa.String(255)))
    op.add_column(
        "conversation_sessions",
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
    )
    op.alter_column("conversation_sessions", "agent_config_id", nullable=True)
    op.alter_column("conversation_sessions", "agent_version_id", nullable=True)
    op.execute(
        "UPDATE conversation_sessions SET "
        "runtime_session_id = 'tenant:' || tenant_id::text || ':session:' || external_session_id, "
        "runtime_user_id = 'tenant:' || tenant_id::text || ':user:' || user_id"
    )
    op.alter_column("conversation_sessions", "runtime_session_id", nullable=False)
    op.alter_column("conversation_sessions", "runtime_user_id", nullable=False)
    op.create_index(
        "uq_conversation_session_tenant_external",
        "conversation_sessions",
        ["tenant_id", "external_session_id"],
        unique=True,
    )
    op.create_index(
        "ix_conversation_sessions_team_config_id",
        "conversation_sessions",
        ["team_config_id"],
    )
    op.create_check_constraint(
        "ck_conversation_session_target",
        "conversation_sessions",
        "(target_type = 'agent' AND agent_config_id IS NOT NULL AND "
        "agent_version_id IS NOT NULL AND team_config_id IS NULL AND team_version_id IS NULL) "
        "OR (target_type = 'team' AND team_config_id IS NOT NULL AND "
        "team_version_id IS NOT NULL AND agent_config_id IS NULL AND agent_version_id IS NULL)",
    )

    # A single synchronized bridge links product governance to Agno's paused run.
    op.add_column("approval_bindings", sa.Column("run_id", sa.String(255)))
    op.add_column("approval_bindings", sa.Column("requirement_id", sa.String(255)))
    op.add_column(
        "approval_bindings",
        sa.Column("requirement", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.add_column("approval_bindings", sa.Column("decision_reason", sa.String(1000)))
    op.add_column("approval_bindings", sa.Column("expires_at", sa.DateTime(timezone=True)))
    op.add_column("approval_bindings", sa.Column("continued_at", sa.DateTime(timezone=True)))
    op.add_column("approval_bindings", sa.Column("continuation_error", sa.String(1000)))
    op.execute(
        "UPDATE approval_bindings SET run_id = 'legacy:' || id::text, "
        "requirement_id = 'legacy:' || id::text WHERE run_id IS NULL"
    )
    op.alter_column("approval_bindings", "run_id", nullable=False)
    op.alter_column("approval_bindings", "requirement_id", nullable=False)
    op.create_index("ix_approval_bindings_run_id", "approval_bindings", ["run_id"])
    op.create_index(
        "uq_approval_requirement",
        "approval_bindings",
        ["tenant_id", "run_id", "requirement_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_approval_requirement", table_name="approval_bindings")
    op.drop_index("ix_approval_bindings_run_id", table_name="approval_bindings")
    for column in (
        "continuation_error",
        "continued_at",
        "expires_at",
        "decision_reason",
        "requirement",
        "requirement_id",
        "run_id",
    ):
        op.drop_column("approval_bindings", column)

    op.drop_constraint("ck_conversation_session_target", "conversation_sessions", type_="check")
    op.drop_index("ix_conversation_sessions_team_config_id", table_name="conversation_sessions")
    op.drop_index("uq_conversation_session_tenant_external", table_name="conversation_sessions")
    op.create_unique_constraint(
        "conversation_sessions_external_session_id_key",
        "conversation_sessions",
        ["external_session_id"],
    )
    for column in (
        "status",
        "last_run_id",
        "runtime_user_id",
        "runtime_session_id",
        "team_version_id",
        "team_config_id",
        "target_type",
    ):
        op.drop_column("conversation_sessions", column)
    op.alter_column("conversation_sessions", "agent_version_id", nullable=False)
    op.alter_column("conversation_sessions", "agent_config_id", nullable=False)

    op.drop_index("uq_knowledge_chunk_content", table_name="knowledge_chunks")
    op.drop_index("uq_knowledge_source_content", table_name="knowledge_sources")
    op.drop_constraint("fk_knowledge_chunk_tenant_source", "knowledge_chunks", type_="foreignkey")
    op.drop_constraint("fk_knowledge_chunk_tenant_base", "knowledge_chunks", type_="foreignkey")
    op.drop_constraint("fk_knowledge_source_tenant_base", "knowledge_sources", type_="foreignkey")
    op.drop_constraint("uq_knowledge_source_tenant_id", "knowledge_sources", type_="unique")
    op.drop_constraint("uq_knowledge_base_tenant_id", "knowledge_bases", type_="unique")
    op.drop_column("knowledge_chunks", "token_count")
    op.drop_column("knowledge_chunks", "content_hash")
    op.drop_column("knowledge_sources", "error_message")
    op.drop_column("knowledge_sources", "chunk_count")
    op.drop_column("knowledge_sources", "embedding_model")
    op.drop_column("knowledge_sources", "content_hash")
