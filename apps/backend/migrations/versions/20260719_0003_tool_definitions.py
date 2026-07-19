"""tenant-scoped reusable tool definitions

Revision ID: 20260719_0003
Revises: 20260719_0002
Create Date: 2026-07-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0003"
down_revision: Union[str, None] = "20260719_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_tenant_credential_tenant_id",
        "tenant_credentials",
        ["tenant_id", "id"],
    )
    op.create_table(
        "tool_definitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("http_method", sa.String(10), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "request_schema", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")
        ),
        sa.Column("response_description", sa.Text()),
        sa.Column("response_schema", sa.JSON()),
        sa.Column("headers", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("config", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("credential_id", sa.Uuid()),
        sa.Column("approval_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
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
            ["tenant_id", "credential_id"],
            ["tenant_credentials.tenant_id", "tenant_credentials.id"],
            ondelete="SET NULL",
            name="fk_tool_definition_tenant_credential",
        ),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_tool_definition_tenant_slug"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tool_definition_tenant_id"),
    )
    op.create_index("ix_tool_definitions_tenant_id", "tool_definitions", ["tenant_id"])

    op.add_column(
        "agent_tool_bindings",
        sa.Column("tool_definition_id", sa.Uuid(), nullable=True),
    )
    op.alter_column("agent_tool_bindings", "tool_key", existing_type=sa.String(100), nullable=True)
    op.drop_constraint(
        "agent_tool_bindings_credential_id_fkey",
        "agent_tool_bindings",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_tool_binding_tenant_definition",
        "agent_tool_bindings",
        "tool_definitions",
        ["tenant_id", "tool_definition_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_tool_binding_tenant_credential",
        "agent_tool_bindings",
        "tenant_credentials",
        ["tenant_id", "credential_id"],
        ["tenant_id", "id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_tool_binding_source",
        "agent_tool_bindings",
        "(tool_key IS NOT NULL) <> (tool_definition_id IS NOT NULL)",
    )
    op.create_index(
        "ix_agent_tool_bindings_tenant_definition",
        "agent_tool_bindings",
        ["tenant_id", "tool_definition_id"],
    )

    op.execute("ALTER TABLE tool_definitions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tool_definitions FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_tool_definitions ON tool_definitions
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_tool_definitions ON tool_definitions")
    op.drop_index("ix_agent_tool_bindings_tenant_definition", table_name="agent_tool_bindings")
    op.drop_constraint("ck_tool_binding_source", "agent_tool_bindings", type_="check")
    op.drop_constraint(
        "fk_tool_binding_tenant_credential", "agent_tool_bindings", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_tool_binding_tenant_definition", "agent_tool_bindings", type_="foreignkey"
    )
    op.create_foreign_key(
        "agent_tool_bindings_credential_id_fkey",
        "agent_tool_bindings",
        "tenant_credentials",
        ["credential_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute("DELETE FROM agent_tool_bindings WHERE tool_key IS NULL")
    op.alter_column("agent_tool_bindings", "tool_key", existing_type=sa.String(100), nullable=False)
    op.drop_column("agent_tool_bindings", "tool_definition_id")
    op.drop_table("tool_definitions")
    op.drop_constraint("uq_tenant_credential_tenant_id", "tenant_credentials", type_="unique")
