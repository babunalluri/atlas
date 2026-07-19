"""add tenant-scoped trace explorer storage

Revision ID: 20260719_0008
Revises: 20260719_0007
Create Date: 2026-07-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0008"
down_revision: Union[str, None] = "20260719_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
        "trace_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_id", sa.String(255)),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("external_session_id", sa.String(255), nullable=False),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("input", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("output", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer()),
        sa.UniqueConstraint("tenant_id", "id", name="uq_trace_record_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["conversation_sessions.tenant_id", "conversation_sessions.id"],
            ondelete="CASCADE",
            name="fk_trace_record_tenant_session",
        ),
    )
    op.create_index("ix_trace_records_tenant_id", "trace_records", ["tenant_id"])
    op.create_index("ix_trace_records_run_id", "trace_records", ["run_id"])
    op.create_index("ix_trace_records_session_id", "trace_records", ["session_id"])
    op.create_index("ix_trace_records_target_id", "trace_records", ["target_id"])
    op.create_index("ix_trace_records_user_id", "trace_records", ["user_id"])
    op.create_index("ix_trace_records_tenant_started", "trace_records", ["tenant_id", "started_at"])
    op.create_index("ix_trace_records_tenant_status", "trace_records", ["tenant_id", "status"])

    op.create_table(
        "trace_spans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("parent_span_id", sa.Uuid()),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attributes", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("input", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("output", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("error", sa.String(2000)),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer()),
        sa.ForeignKeyConstraint(
            ["tenant_id", "trace_id"],
            ["trace_records.tenant_id", "trace_records.id"],
            ondelete="CASCADE",
            name="fk_trace_span_tenant_trace",
        ),
    )
    op.create_index("ix_trace_spans_tenant_id", "trace_spans", ["tenant_id"])
    op.create_index("ix_trace_spans_trace_id", "trace_spans", ["trace_id"])
    op.create_index(
        "ix_trace_spans_trace_sequence",
        "trace_spans",
        ["tenant_id", "trace_id", "sequence"],
    )
    _enable_rls("trace_records")
    _enable_rls("trace_spans")


def downgrade() -> None:
    for table in ("trace_spans", "trace_records"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_table("trace_spans")
    op.drop_table("trace_records")
