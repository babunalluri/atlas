"""Add tenant_python versions and platform sandbox package allowlist.

Revision ID: 20260719_0016
Revises: 20260719_0015
"""

import sqlalchemy as sa
from alembic import op

revision = "20260719_0016"
down_revision = "20260719_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_tool_definition_provider", "tool_definitions", type_="check")
    op.create_check_constraint(
        "ck_tool_definition_provider",
        "tool_definitions",
        "kind IN ('http', 'openapi', 'python_toolkit', 'custom_python', 'mcp', 'tenant_python')",
    )

    op.add_column(
        "tool_definitions",
        sa.Column("published_version_id", sa.Uuid(), nullable=True),
    )

    op.create_table(
        "tool_definition_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("tool_definition_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("source_code", sa.Text(), nullable=False),
        sa.Column("dependencies", sa.JSON(), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("entry_module", sa.String(length=100), nullable=False, server_default="tool"),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "tool_definition_id"],
            ["tool_definitions.tenant_id", "tool_definitions.id"],
            ondelete="CASCADE",
            name="fk_tool_definition_version_tenant_config",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "tool_definition_id",
            "version",
            name="uq_tool_definition_version",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "tool_definition_id",
            "id",
            name="uq_tool_definition_version_tenant_config_id",
        ),
    )
    op.create_index(
        "ix_tool_definition_versions_tenant_id",
        "tool_definition_versions",
        ["tenant_id"],
    )
    op.create_index(
        "ix_tool_definition_versions_tool_definition_id",
        "tool_definition_versions",
        ["tool_definition_id"],
    )
    op.execute("ALTER TABLE tool_definition_versions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tool_definition_versions FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_tool_definition_versions
        ON tool_definition_versions
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )

    # Soft pin only — matches agent/team published_version_id (no hard FK cycle).
    op.create_index(
        "ix_tool_definitions_published_version_id",
        "tool_definitions",
        ["published_version_id"],
    )

    op.create_table(
        "platform_python_packages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_platform_python_package_name_version"),
    )
    op.create_index(
        "ix_platform_python_packages_name",
        "platform_python_packages",
        ["name"],
    )
    op.create_index(
        "ix_platform_python_packages_active",
        "platform_python_packages",
        ["active"],
    )

    # Seed a minimal allowlist for local development (hashes are placeholders
    # for image rebuild; runtime validates name+version membership).
    op.execute(
        """
        INSERT INTO platform_python_packages (id, name, version, sha256, active)
        VALUES
          ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1', 'jsonschema', '4.26.0',
           'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', true),
          ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2', 'pydantic', '2.13.4',
           'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', true)
        """
    )


def downgrade() -> None:
    op.drop_index("ix_platform_python_packages_active", table_name="platform_python_packages")
    op.drop_index("ix_platform_python_packages_name", table_name="platform_python_packages")
    op.drop_table("platform_python_packages")
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_tool_definition_versions "
        "ON tool_definition_versions"
    )
    op.drop_index(
        "ix_tool_definition_versions_tool_definition_id",
        table_name="tool_definition_versions",
    )
    op.drop_index(
        "ix_tool_definition_versions_tenant_id",
        table_name="tool_definition_versions",
    )
    op.drop_table("tool_definition_versions")
    op.drop_index("ix_tool_definitions_published_version_id", table_name="tool_definitions")
    op.drop_column("tool_definitions", "published_version_id")
    op.drop_constraint("ck_tool_definition_provider", "tool_definitions", type_="check")
    op.create_check_constraint(
        "ck_tool_definition_provider",
        "tool_definitions",
        "kind IN ('http', 'openapi', 'python_toolkit', 'mcp')",
    )
