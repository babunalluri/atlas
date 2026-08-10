"""Rename leftover tenants clerk_org unique index to auth_org.

Revision ID: 20260810_0007
Revises: 20260810_0006
"""

from alembic import op

revision = "20260810_0007"
down_revision = "20260810_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL keeps the old index name after column rename.
    op.execute(
        "ALTER INDEX IF EXISTS tenants_clerk_org_id_key "
        "RENAME TO tenants_auth_org_id_key"
    )


def downgrade() -> None:
    op.execute(
        "ALTER INDEX IF EXISTS tenants_auth_org_id_key "
        "RENAME TO tenants_clerk_org_id_key"
    )
