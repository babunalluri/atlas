"""Prepaid billing: plans, wallets, ledger.

Revision ID: 20260810_0004
Revises: 20260810_0003
"""

import sqlalchemy as sa
from alembic import op

revision = "20260810_0004"
down_revision = "20260810_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "billing_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("monthly_price_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("included_credits_monthly", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "credits_per_1k_input_tokens",
            sa.Integer(),
            nullable=False,
            server_default="10",
        ),
        sa.Column(
            "credits_per_1k_output_tokens",
            sa.Integer(),
            nullable=False,
            server_default="30",
        ),
        sa.Column("credit_pack_credits", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("credit_pack_price_cents", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("stripe_monthly_price_id", sa.String(length=255), nullable=True),
        sa.Column("stripe_credit_pack_price_id", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
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
        sa.CheckConstraint("scope IN ('platform', 'tenant')", name="ck_billing_plans_scope"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope", "tenant_id", "slug", name="uq_billing_plan_scope_slug"),
    )
    op.create_index("ix_billing_plans_tenant_id", "billing_plans", ["tenant_id"])

    op.create_table(
        "billing_wallets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("owner_type", sa.String(length=16), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("balance_credits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("allowance_remaining", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("plan_id", sa.Uuid(), nullable=True),
        sa.Column("subscription_status", sa.String(length=32), nullable=False, server_default="none"),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True),
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
            "owner_type IN ('tenant', 'user')", name="ck_billing_wallets_owner_type"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["billing_plans.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "owner_type",
            "owner_id",
            name="uq_billing_wallet_owner",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_billing_wallet_tenant_id"),
    )
    op.create_index("ix_billing_wallets_tenant_id", "billing_wallets", ["tenant_id"])

    op.create_table(
        "billing_ledger",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("wallet_id", sa.Uuid(), nullable=False),
        sa.Column("entry_type", sa.String(length=32), nullable=False),
        sa.Column("amount_credits", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("reference_type", sa.String(length=32), nullable=True),
        sa.Column("reference_id", sa.String(length=255), nullable=True),
        sa.Column("description", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(length=255), nullable=False, server_default="system"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "wallet_id"],
            ["billing_wallets.tenant_id", "billing_wallets.id"],
            ondelete="CASCADE",
            name="fk_billing_ledger_wallet",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_billing_ledger_tenant_id"),
    )
    op.create_index("ix_billing_ledger_wallet", "billing_ledger", ["tenant_id", "wallet_id"])
    op.create_index(
        "ix_billing_ledger_created", "billing_ledger", ["tenant_id", "created_at"]
    )

    op.execute("ALTER TABLE billing_wallets ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE billing_wallets FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_billing_wallets ON billing_wallets
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    op.execute("ALTER TABLE billing_ledger ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE billing_ledger FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_billing_ledger ON billing_ledger
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_billing_ledger ON billing_ledger")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_billing_wallets ON billing_wallets")
    op.drop_index("ix_billing_ledger_created", table_name="billing_ledger")
    op.drop_index("ix_billing_ledger_wallet", table_name="billing_ledger")
    op.drop_table("billing_ledger")
    op.drop_index("ix_billing_wallets_tenant_id", table_name="billing_wallets")
    op.drop_table("billing_wallets")
    op.drop_index("ix_billing_plans_tenant_id", table_name="billing_plans")
    op.drop_table("billing_plans")
