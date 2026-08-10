"""Razorpay ids on billing plans and wallets.

Revision ID: 20260810_0005
Revises: 20260810_0004
"""

import sqlalchemy as sa
from alembic import op

revision = "20260810_0005"
down_revision = "20260810_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "billing_plans",
        sa.Column("razorpay_monthly_plan_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "billing_wallets",
        sa.Column("razorpay_customer_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("billing_wallets", "razorpay_customer_id")
    op.drop_column("billing_plans", "razorpay_monthly_plan_id")
