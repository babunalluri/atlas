"""Extend memberships for tenant user directory CRUD.

Revision ID: 20260719_0015
Revises: 20260719_0014
"""

import sqlalchemy as sa
from alembic import op

revision = "20260719_0015"
down_revision = "20260719_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memberships",
        sa.Column(
            "display_name",
            sa.String(length=255),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column("memberships", sa.Column("email", sa.String(length=320), nullable=True))
    op.add_column(
        "memberships",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.execute(
        """
        UPDATE memberships
        SET display_name = user_id
        WHERE display_name = '' OR display_name IS NULL
        """
    )
    op.alter_column("memberships", "display_name", server_default=None)
    op.alter_column("memberships", "is_active", server_default=None)


def downgrade() -> None:
    op.drop_column("memberships", "is_active")
    op.drop_column("memberships", "email")
    op.drop_column("memberships", "display_name")
