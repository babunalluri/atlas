"""Add team_tool_bindings for team-level tool attachments.

Revision ID: 20260719_0017
Revises: 20260719_0016
"""

import sqlalchemy as sa
from alembic import op

revision = "20260719_0017"
down_revision = "20260719_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "team_tool_bindings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "team_version_id",
            sa.Uuid(),
            sa.ForeignKey("team_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_key", sa.String(100), nullable=True),
        sa.Column("tool_definition_id", sa.Uuid(), nullable=True),
        sa.Column(
            "config", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")
        ),
        sa.Column("credential_id", sa.Uuid(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "tool_definition_id"],
            ["tool_definitions.tenant_id", "tool_definitions.id"],
            ondelete="RESTRICT",
            name="fk_team_tool_binding_tenant_definition",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "credential_id"],
            ["tenant_credentials.tenant_id", "tenant_credentials.id"],
            ondelete="SET NULL",
            name="fk_team_tool_binding_tenant_credential",
        ),
        sa.CheckConstraint(
            "(tool_key IS NOT NULL) <> (tool_definition_id IS NOT NULL)",
            name="ck_team_tool_binding_source",
        ),
    )
    op.create_index("ix_team_tool_bindings_tenant_id", "team_tool_bindings", ["tenant_id"])
    op.create_index(
        "ix_team_tool_bindings_team_version_id",
        "team_tool_bindings",
        ["team_version_id"],
    )
    op.create_index(
        "ix_team_tool_bindings_tenant_definition",
        "team_tool_bindings",
        ["tenant_id", "tool_definition_id"],
    )

    op.execute("ALTER TABLE team_tool_bindings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE team_tool_bindings FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_team_tool_bindings ON team_tool_bindings
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_team_tool_bindings ON team_tool_bindings")
    op.drop_index("ix_team_tool_bindings_tenant_definition", table_name="team_tool_bindings")
    op.drop_index("ix_team_tool_bindings_team_version_id", table_name="team_tool_bindings")
    op.drop_index("ix_team_tool_bindings_tenant_id", table_name="team_tool_bindings")
    op.drop_table("team_tool_bindings")
