"""add tenant-scoped schedules

Revision ID: 20260719_0011
Revises: 20260719_0010
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0011"
down_revision: str | None = "20260719_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ["schedules", "schedule_runs"]


def _tenant_column() -> sa.Column:
    return sa.Column(
        "tenant_id",
        sa.Uuid(),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation_{table} ON {table}
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def upgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant_column(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("cron_expression", sa.String(255), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False, server_default="UTC"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("agent_config_id", sa.Uuid()),
        sa.Column("agent_version_id", sa.Uuid()),
        sa.Column("team_config_id", sa.Uuid()),
        sa.Column("team_version_id", sa.Uuid()),
        sa.Column("workflow_config_id", sa.Uuid()),
        sa.Column("workflow_version_id", sa.Uuid()),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("input_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_status", sa.String(32)),
        sa.Column("last_error", sa.String(2000)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_schedule_tenant_id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_schedule_tenant_name"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "agent_config_id", "agent_version_id"],
            ["agent_versions.tenant_id", "agent_versions.agent_config_id", "agent_versions.id"],
            ondelete="RESTRICT",
            name="fk_schedule_agent_version",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "team_config_id", "team_version_id"],
            ["team_versions.tenant_id", "team_versions.team_config_id", "team_versions.id"],
            ondelete="RESTRICT",
            name="fk_schedule_team_version",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workflow_config_id", "workflow_version_id"],
            [
                "workflow_versions.tenant_id",
                "workflow_versions.workflow_config_id",
                "workflow_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_schedule_workflow_version",
        ),
        sa.CheckConstraint(
            "(target_type = 'agent' AND agent_config_id IS NOT NULL AND "
            "agent_version_id IS NOT NULL AND team_config_id IS NULL AND "
            "team_version_id IS NULL AND workflow_config_id IS NULL AND "
            "workflow_version_id IS NULL) OR "
            "(target_type = 'team' AND team_config_id IS NOT NULL AND "
            "team_version_id IS NOT NULL AND agent_config_id IS NULL AND "
            "agent_version_id IS NULL AND workflow_config_id IS NULL AND "
            "workflow_version_id IS NULL) OR "
            "(target_type = 'workflow' AND workflow_config_id IS NOT NULL AND "
            "workflow_version_id IS NOT NULL AND agent_config_id IS NULL AND "
            "agent_version_id IS NULL AND team_config_id IS NULL AND team_version_id IS NULL)",
            name="ck_schedule_target",
        ),
    )
    op.create_index("ix_schedules_tenant_id", "schedules", ["tenant_id"])
    op.create_index("ix_schedules_next_run_at", "schedules", ["next_run_at"])
    op.create_index(
        "ix_schedules_tenant_due",
        "schedules",
        ["tenant_id", "enabled", "next_run_at"],
    )

    op.create_table(
        "schedule_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant_column(),
        sa.Column("schedule_id", sa.Uuid(), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("session_id", sa.String(255), nullable=False),
        sa.Column("run_id", sa.String(255)),
        sa.Column("output", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("error", sa.String(2000)),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "id", name="uq_schedule_run_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "schedule_id"],
            ["schedules.tenant_id", "schedules.id"],
            ondelete="CASCADE",
            name="fk_schedule_run_tenant_schedule",
        ),
    )
    op.create_index("ix_schedule_runs_tenant_id", "schedule_runs", ["tenant_id"])
    op.create_index("ix_schedule_runs_schedule_id", "schedule_runs", ["schedule_id"])
    op.create_index(
        "ix_schedule_runs_tenant_started",
        "schedule_runs",
        ["tenant_id", "started_at"],
    )

    for table in TABLES:
        _enable_rls(table)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.drop_table(table)
