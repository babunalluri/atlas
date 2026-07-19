"""Add platform-admin audit events.

Revision ID: 20260719_0013
Revises: 20260719_0012
"""

import sqlalchemy as sa
from alembic import op

revision = "20260719_0013"
down_revision = "20260719_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_platform_audit_events_actor_id",
        "platform_audit_events",
        ["actor_id"],
    )
    op.create_index(
        "ix_platform_audit_events_tenant_id",
        "platform_audit_events",
        ["tenant_id"],
    )
    op.create_index(
        "ix_platform_audit_events_created_at",
        "platform_audit_events",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_platform_audit_events_created_at",
        table_name="platform_audit_events",
    )
    op.drop_index(
        "ix_platform_audit_events_tenant_id",
        table_name="platform_audit_events",
    )
    op.drop_index(
        "ix_platform_audit_events_actor_id",
        table_name="platform_audit_events",
    )
    op.drop_table("platform_audit_events")
