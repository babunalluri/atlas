"""Add optional phone to memberships.

Revision ID: 20260810_0002
Revises: 20260810_0001
"""

import sqlalchemy as sa
from alembic import op

revision = "20260810_0002"
down_revision = "20260810_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memberships",
        sa.Column("phone", sa.String(length=40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("memberships", "phone")
