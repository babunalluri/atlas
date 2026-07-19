"""add tenant-scoped MCP settings

Revision ID: 20260719_0012
Revises: 20260719_0011
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0012"
down_revision: str | None = "20260719_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_mcp_settings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_mcp_settings_tenant"),
    )
    op.create_index(
        "ix_tenant_mcp_settings_tenant_id",
        "tenant_mcp_settings",
        ["tenant_id"],
    )
    op.execute("ALTER TABLE tenant_mcp_settings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_mcp_settings FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_tenant_mcp_settings ON tenant_mcp_settings
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_tenant_mcp_settings "
        "ON tenant_mcp_settings"
    )
    op.execute("ALTER TABLE tenant_mcp_settings DISABLE ROW LEVEL SECURITY")
    op.drop_table("tenant_mcp_settings")
