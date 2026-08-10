"""Add user_notifications for in-app org messaging.

Revision ID: 20260810_0003
Revises: 20260810_0002
"""

import sqlalchemy as sa
from alembic import op

revision = "20260810_0003"
down_revision = "20260810_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("audience", sa.String(length=16), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("audience IN ('user', 'all')", name="ck_user_notifications_audience"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_user_notifications_tenant_id"),
    )
    op.create_index(
        "ix_user_notifications_tenant_id",
        "user_notifications",
        ["tenant_id"],
    )
    op.create_index(
        "ix_user_notifications_user",
        "user_notifications",
        ["tenant_id", "user_id", "created_at"],
    )
    op.create_index(
        "ix_user_notifications_batch",
        "user_notifications",
        ["tenant_id", "batch_id"],
    )
    op.create_index(
        "ix_user_notifications_unread",
        "user_notifications",
        ["tenant_id", "user_id"],
        postgresql_where=sa.text("read_at IS NULL"),
        sqlite_where=sa.text("read_at IS NULL"),
    )
    op.execute("ALTER TABLE user_notifications ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE user_notifications FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_user_notifications ON user_notifications
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_user_notifications ON user_notifications"
    )
    op.drop_index("ix_user_notifications_unread", table_name="user_notifications")
    op.drop_index("ix_user_notifications_batch", table_name="user_notifications")
    op.drop_index("ix_user_notifications_user", table_name="user_notifications")
    op.drop_index("ix_user_notifications_tenant_id", table_name="user_notifications")
    op.drop_table("user_notifications")
