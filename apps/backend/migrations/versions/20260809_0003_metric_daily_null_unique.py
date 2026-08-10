"""Enforce unique metric dimensions when target_id is NULL.

Postgres UNIQUE treats NULL as distinct, so concurrent/corrupt inserts could
duplicate "all"-target daily rows. Replace the table unique constraint with an
expression index that coalesces NULL target_id to a sentinel UUID.

Revision ID: 20260809_0003
Revises: 20260809_0002
"""

from alembic import op

revision = "20260809_0003"
down_revision = "20260809_0002"
branch_labels = None
depends_on = None

_SENTINEL = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    # Keep one row per dimension; prefer the newest when NULL target_id dupes exist.
    op.execute(
        """
        DELETE FROM metric_daily_aggregates a
        USING metric_daily_aggregates b
        WHERE a.target_id IS NULL
          AND b.target_id IS NULL
          AND a.tenant_id = b.tenant_id
          AND a.metric_date = b.metric_date
          AND a.target_type = b.target_type
          AND a.ctid < b.ctid
        """
    )
    op.drop_constraint(
        "uq_metric_daily_dimension",
        "metric_daily_aggregates",
        type_="unique",
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_metric_daily_dimension
        ON metric_daily_aggregates (
            tenant_id,
            metric_date,
            target_type,
            COALESCE(target_id, '{_SENTINEL}'::uuid)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_metric_daily_dimension")
    op.create_unique_constraint(
        "uq_metric_daily_dimension",
        "metric_daily_aggregates",
        ["tenant_id", "metric_date", "target_type", "target_id"],
    )
