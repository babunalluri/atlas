"""Add workspace domain to tenants."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260811_0002"
down_revision = "20260811_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "domain",
            sa.String(length=50),
            nullable=False,
            server_default="generic",
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "domain")
