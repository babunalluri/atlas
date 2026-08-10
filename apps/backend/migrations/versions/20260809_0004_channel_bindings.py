"""Add channel_bindings for Slack/Telegram/WhatsApp.

Revision ID: 20260809_0004
Revises: 20260809_0003
"""

import sqlalchemy as sa
from alembic import op

revision = "20260809_0004"
down_revision = "20260809_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("credential_id", sa.Uuid(), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_config_id", sa.Uuid(), nullable=False),
        sa.Column("external_config", sa.JSON(), nullable=False),
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
        sa.CheckConstraint(
            "provider IN ('slack', 'telegram', 'whatsapp')",
            name="ck_channel_bindings_provider",
        ),
        sa.CheckConstraint(
            "target_type IN ('team', 'workflow')",
            name="ck_channel_bindings_target_type",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "credential_id"],
            ["tenant_credentials.tenant_id", "tenant_credentials.id"],
            ondelete="RESTRICT",
            name="fk_channel_binding_tenant_credential",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_channel_binding_tenant_id"),
    )
    op.create_index(
        "ix_channel_bindings_tenant_id",
        "channel_bindings",
        ["tenant_id"],
    )
    op.create_index(
        "ix_channel_bindings_provider",
        "channel_bindings",
        ["tenant_id", "provider"],
    )
    op.execute("ALTER TABLE channel_bindings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE channel_bindings FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_channel_bindings ON channel_bindings
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_channel_bindings ON channel_bindings")
    op.drop_index("ix_channel_bindings_provider", table_name="channel_bindings")
    op.drop_index("ix_channel_bindings_tenant_id", table_name="channel_bindings")
    op.drop_table("channel_bindings")
