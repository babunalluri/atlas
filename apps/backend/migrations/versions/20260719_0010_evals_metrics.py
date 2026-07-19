"""add tenant-scoped evals and metric aggregates

Revision ID: 20260719_0010
Revises: 20260719_0009
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0010"
down_revision: str | None = "20260719_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = [
    "eval_definitions",
    "eval_runs",
    "eval_case_results",
    "metric_daily_aggregates",
]


def _tenant_column() -> sa.Column:
    return sa.Column(
        "tenant_id",
        sa.Uuid(),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )


def _timestamps() -> list[sa.Column]:
    return [
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
    ]


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
        "eval_definitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant_column(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("suite", sa.String(32), nullable=False, server_default="smoke"),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("agent_config_id", sa.Uuid()),
        sa.Column("agent_version_id", sa.Uuid()),
        sa.Column("team_config_id", sa.Uuid()),
        sa.Column("team_version_id", sa.Uuid()),
        sa.Column("workflow_config_id", sa.Uuid()),
        sa.Column("workflow_version_id", sa.Uuid()),
        sa.Column("cases", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("pass_threshold", sa.Float(), nullable=False, server_default="1"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("run_on_publish", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(255), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "id", name="uq_eval_definition_tenant_id"),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_eval_definition_tenant_slug"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "agent_config_id", "agent_version_id"],
            ["agent_versions.tenant_id", "agent_versions.agent_config_id", "agent_versions.id"],
            ondelete="CASCADE",
            name="fk_eval_definition_agent_version",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "team_config_id", "team_version_id"],
            ["team_versions.tenant_id", "team_versions.team_config_id", "team_versions.id"],
            ondelete="CASCADE",
            name="fk_eval_definition_team_version",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workflow_config_id", "workflow_version_id"],
            [
                "workflow_versions.tenant_id",
                "workflow_versions.workflow_config_id",
                "workflow_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_eval_definition_workflow_version",
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
            name="ck_eval_definition_target",
        ),
        sa.CheckConstraint(
            "pass_threshold >= 0 AND pass_threshold <= 1",
            name="ck_eval_definition_threshold",
        ),
    )
    op.create_index("ix_eval_definitions_tenant_id", "eval_definitions", ["tenant_id"])
    op.create_index(
        "ix_eval_definitions_tenant_suite", "eval_definitions", ["tenant_id", "suite"]
    )

    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant_column(),
        sa.Column("eval_definition_id", sa.Uuid(), nullable=False),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("trigger", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("score", sa.Float()),
        sa.Column("passed", sa.Boolean()),
        sa.Column("total_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passed_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("estimated_cost_usd", sa.Float()),
        sa.Column("error", sa.String(2000)),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "id", name="uq_eval_run_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "eval_definition_id"],
            ["eval_definitions.tenant_id", "eval_definitions.id"],
            ondelete="CASCADE",
            name="fk_eval_run_tenant_definition",
        ),
    )
    op.create_index("ix_eval_runs_tenant_id", "eval_runs", ["tenant_id"])
    op.create_index("ix_eval_runs_eval_definition_id", "eval_runs", ["eval_definition_id"])
    op.create_index("ix_eval_runs_tenant_started", "eval_runs", ["tenant_id", "started_at"])

    op.create_table(
        "eval_case_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant_column(),
        sa.Column("eval_run_id", sa.Uuid(), nullable=False),
        sa.Column("case_key", sa.String(100), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column("expected_output", sa.Text(), nullable=False),
        sa.Column("actual_output", sa.Text()),
        sa.Column("evaluator", sa.String(32), nullable=False, server_default="contains"),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("estimated_cost_usd", sa.Float()),
        sa.Column("error", sa.String(2000)),
        sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "eval_run_id"],
            ["eval_runs.tenant_id", "eval_runs.id"],
            ondelete="CASCADE",
            name="fk_eval_case_result_tenant_run",
        ),
        sa.UniqueConstraint(
            "tenant_id", "eval_run_id", "case_key", name="uq_eval_case_result_run_key"
        ),
    )
    op.create_index("ix_eval_case_results_tenant_id", "eval_case_results", ["tenant_id"])
    op.create_index("ix_eval_case_results_eval_run_id", "eval_case_results", ["eval_run_id"])

    op.create_table(
        "metric_daily_aggregates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _tenant_column(),
        sa.Column("metric_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("target_id", sa.Uuid()),
        sa.Column("run_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("paused_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_p50_ms", sa.Integer()),
        sa.Column("latency_p95_ms", sa.Integer()),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("tool_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("approval_waits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unique_sessions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("top_tools", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        *_timestamps(),
        sa.UniqueConstraint(
            "tenant_id",
            "metric_date",
            "target_type",
            "target_id",
            name="uq_metric_daily_dimension",
        ),
    )
    op.create_index(
        "ix_metric_daily_aggregates_tenant_id", "metric_daily_aggregates", ["tenant_id"]
    )
    op.create_index(
        "ix_metric_daily_tenant_date",
        "metric_daily_aggregates",
        ["tenant_id", "metric_date"],
    )

    for table in TABLES:
        _enable_rls(table)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.drop_table(table)
