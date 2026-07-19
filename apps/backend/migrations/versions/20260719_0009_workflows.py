"""add tenant-scoped immutable workflows

Revision ID: 20260719_0009
Revises: 20260719_0008
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260719_0009"
down_revision: str | None = "20260719_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKFLOW_TABLES = ["workflow_configs", "workflow_versions", "workflow_steps"]


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation_{table} ON {table}
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def upgrade() -> None:
    op.create_table(
        "workflow_configs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("published_version_id", sa.Uuid()),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_workflow_tenant_slug"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_workflow_config_tenant_id"),
    )
    op.create_index("ix_workflow_configs_tenant_id", "workflow_configs", ["tenant_id"])

    op.create_table(
        "workflow_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_config_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "draft", "published", "archived", name="agentstatus", create_type=False
            ),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("mode", sa.String(32), nullable=False, server_default="sequential"),
        sa.Column("created_by", sa.String(255), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workflow_config_id"],
            ["workflow_configs.tenant_id", "workflow_configs.id"],
            ondelete="CASCADE",
            name="fk_workflow_version_tenant_config",
        ),
        sa.UniqueConstraint(
            "tenant_id", "workflow_config_id", "version", name="uq_workflow_version"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "workflow_config_id",
            "id",
            name="uq_workflow_version_tenant_config_id",
        ),
    )
    op.create_index("ix_workflow_versions_tenant_id", "workflow_versions", ["tenant_id"])
    op.create_index(
        "ix_workflow_versions_workflow_config_id",
        "workflow_versions",
        ["workflow_config_id"],
    )

    op.create_table(
        "workflow_steps",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_config_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_version_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("agent_config_id", sa.Uuid()),
        sa.Column("agent_version_id", sa.Uuid()),
        sa.Column("team_config_id", sa.Uuid()),
        sa.Column("team_version_id", sa.Uuid()),
        sa.Column("condition_expression", sa.String(1000)),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workflow_config_id", "workflow_version_id"],
            [
                "workflow_versions.tenant_id",
                "workflow_versions.workflow_config_id",
                "workflow_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_workflow_step_tenant_workflow_version",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "agent_config_id", "agent_version_id"],
            ["agent_versions.tenant_id", "agent_versions.agent_config_id", "agent_versions.id"],
            ondelete="RESTRICT",
            name="fk_workflow_step_tenant_agent_version",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "team_config_id", "team_version_id"],
            ["team_versions.tenant_id", "team_versions.team_config_id", "team_versions.id"],
            ondelete="RESTRICT",
            name="fk_workflow_step_tenant_team_version",
        ),
        sa.CheckConstraint(
            "(target_type = 'agent' AND agent_config_id IS NOT NULL AND "
            "agent_version_id IS NOT NULL AND team_config_id IS NULL AND team_version_id IS NULL) "
            "OR (target_type = 'team' AND team_config_id IS NOT NULL AND "
            "team_version_id IS NOT NULL AND agent_config_id IS NULL AND agent_version_id IS NULL)",
            name="ck_workflow_step_target",
        ),
    )
    op.create_index("ix_workflow_steps_tenant_id", "workflow_steps", ["tenant_id"])
    op.create_index(
        "ix_workflow_steps_workflow_version_id", "workflow_steps", ["workflow_version_id"]
    )
    op.create_index(
        "uq_workflow_step_position",
        "workflow_steps",
        ["workflow_version_id", "position"],
        unique=True,
    )

    for table in WORKFLOW_TABLES:
        _enable_rls(table)

    op.add_column("conversation_sessions", sa.Column("workflow_config_id", sa.Uuid()))
    op.add_column("conversation_sessions", sa.Column("workflow_version_id", sa.Uuid()))
    op.create_index(
        "ix_conversation_sessions_workflow_config_id",
        "conversation_sessions",
        ["workflow_config_id"],
    )
    op.create_foreign_key(
        "fk_conversation_workflow_version",
        "conversation_sessions",
        "workflow_versions",
        ["tenant_id", "workflow_config_id", "workflow_version_id"],
        ["tenant_id", "workflow_config_id", "id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "ck_conversation_session_target", "conversation_sessions", type_="check"
    )
    op.create_check_constraint(
        "ck_conversation_session_target",
        "conversation_sessions",
        "(target_type = 'agent' AND agent_config_id IS NOT NULL AND "
        "agent_version_id IS NOT NULL AND team_config_id IS NULL AND team_version_id IS NULL "
        "AND workflow_config_id IS NULL AND workflow_version_id IS NULL) "
        "OR (target_type = 'team' AND team_config_id IS NOT NULL AND "
        "team_version_id IS NOT NULL AND agent_config_id IS NULL AND agent_version_id IS NULL "
        "AND workflow_config_id IS NULL AND workflow_version_id IS NULL) "
        "OR (target_type = 'workflow' AND workflow_config_id IS NOT NULL AND "
        "workflow_version_id IS NOT NULL AND agent_config_id IS NULL AND agent_version_id IS NULL "
        "AND team_config_id IS NULL AND team_version_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_conversation_session_target", "conversation_sessions", type_="check"
    )
    op.create_check_constraint(
        "ck_conversation_session_target",
        "conversation_sessions",
        "(target_type = 'agent' AND agent_config_id IS NOT NULL AND "
        "agent_version_id IS NOT NULL AND team_config_id IS NULL AND team_version_id IS NULL) "
        "OR (target_type = 'team' AND team_config_id IS NOT NULL AND "
        "team_version_id IS NOT NULL AND agent_config_id IS NULL AND agent_version_id IS NULL)",
    )
    op.drop_constraint(
        "fk_conversation_workflow_version", "conversation_sessions", type_="foreignkey"
    )
    op.drop_index(
        "ix_conversation_sessions_workflow_config_id", table_name="conversation_sessions"
    )
    op.drop_column("conversation_sessions", "workflow_version_id")
    op.drop_column("conversation_sessions", "workflow_config_id")
    for table in reversed(WORKFLOW_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.drop_table(table)
