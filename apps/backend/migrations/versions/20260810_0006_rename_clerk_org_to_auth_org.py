"""Rename tenants.clerk_org_id → tenants.auth_org_id.

Revision ID: 20260810_0006
Revises: 20260810_0005
"""

from alembic import op

revision = "20260810_0006"
down_revision = "20260810_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("tenants", "clerk_org_id", new_column_name="auth_org_id")


def downgrade() -> None:
    op.alter_column("tenants", "auth_org_id", new_column_name="clerk_org_id")
