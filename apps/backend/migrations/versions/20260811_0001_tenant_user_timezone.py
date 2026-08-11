"""Add IANA timezone columns for tenants and memberships."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260811_0001"
down_revision = "20260810_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "timezone",
            sa.String(length=100),
            nullable=False,
            server_default="UTC",
        ),
    )
    op.add_column(
        "memberships",
        sa.Column(
            "timezone",
            sa.String(length=100),
            nullable=False,
            server_default="UTC",
        ),
    )


def downgrade() -> None:
    op.drop_column("memberships", "timezone")
    op.drop_column("tenants", "timezone")
