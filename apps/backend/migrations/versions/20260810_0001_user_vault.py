"""Add user_vault_entries for per-user secrets and variables.

Revision ID: 20260810_0001
Revises: 20260809_0004
"""

import sqlalchemy as sa
from alembic import op

revision = "20260810_0001"
down_revision = "20260809_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_vault_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("key_version", sa.String(length=32), nullable=False),
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
        sa.CheckConstraint("kind IN ('secret', 'variable')", name="ck_user_vault_kind"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            "name",
            name="uq_user_vault_tenant_user_name",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_user_vault_tenant_id"),
    )
    op.create_index("ix_user_vault_entries_tenant_id", "user_vault_entries", ["tenant_id"])
    op.create_index(
        "ix_user_vault_entries_user",
        "user_vault_entries",
        ["tenant_id", "user_id"],
    )
    op.execute("ALTER TABLE user_vault_entries ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE user_vault_entries FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_user_vault_entries ON user_vault_entries
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_user_vault_entries ON user_vault_entries"
    )
    op.drop_index("ix_user_vault_entries_user", table_name="user_vault_entries")
    op.drop_index("ix_user_vault_entries_tenant_id", table_name="user_vault_entries")
    op.drop_table("user_vault_entries")
