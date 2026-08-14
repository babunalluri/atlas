"""Stamp workspace domain on agent, team, and workflow configs."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260814_0001"
down_revision = "20260811_0002"
branch_labels = None
depends_on = None

STOCK_BROKER_SLUGS = (
    "learning",
    "learning-guide",
    "paper-trading",
    "paper-trader",
    "paper-from-signal",
    "live-trading",
    "live-trader",
    "live-approval",
)
DENTAL_SLUGS = (
    "front-desk",
    "patient-concierge",
    "clinician-copilot",
    "front-desk-team",
    "patient-support",
    "book-appointment",
    "recall-reminder",
)


def upgrade() -> None:
    for table in ("agent_configs", "team_configs", "workflow_configs"):
        op.add_column(
            table,
            sa.Column(
                "domain",
                sa.String(length=50),
                nullable=False,
                server_default="generic",
            ),
        )

    bind = op.get_bind()
    for table in ("agent_configs", "team_configs", "workflow_configs"):
        bind.execute(
            sa.text(
                f"UPDATE {table} AS cfg SET domain = t.domain "
                "FROM tenants AS t WHERE cfg.tenant_id = t.id "
                "AND t.domain IS NOT NULL AND t.domain <> 'generic'"
            )
        )
        for domain, slugs in (
            ("stock_broker", STOCK_BROKER_SLUGS),
            ("dental_clinic", DENTAL_SLUGS),
        ):
            for slug in slugs:
                bind.execute(
                    sa.text(
                        f"UPDATE {table} SET domain = :domain "
                        "WHERE slug = :slug OR slug = :copy OR slug LIKE :copy_n"
                    ),
                    {
                        "domain": domain,
                        "slug": slug,
                        "copy": f"{slug}-copy",
                        "copy_n": f"{slug}-copy-%",
                    },
                )


def downgrade() -> None:
    for table in ("agent_configs", "team_configs", "workflow_configs"):
        op.drop_column(table, "domain")
