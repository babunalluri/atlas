"""Add tenant-scoped workflow user assignments.

Revision ID: 20260719_0014
Revises: 20260719_0013
"""

import sqlalchemy as sa
from alembic import op

revision = "20260719_0014"
down_revision = "20260719_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_config_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("assigned_by", sa.String(length=255), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workflow_config_id"],
            ["workflow_configs.tenant_id", "workflow_configs.id"],
            ondelete="CASCADE",
            name="fk_workflow_assignment_tenant_config",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "workflow_config_id",
            "user_id",
            name="uq_workflow_assignment_tenant_workflow_user",
        ),
    )
    op.create_index(
        "ix_workflow_assignments_tenant_id",
        "workflow_assignments",
        ["tenant_id"],
    )
    op.create_index(
        "ix_workflow_assignments_workflow_config_id",
        "workflow_assignments",
        ["workflow_config_id"],
    )
    op.create_index(
        "ix_workflow_assignments_user_id",
        "workflow_assignments",
        ["user_id"],
    )
    op.execute("ALTER TABLE workflow_assignments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workflow_assignments FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_workflow_assignments ON workflow_assignments
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_assignments_user_id",
        table_name="workflow_assignments",
    )
    op.drop_index(
        "ix_workflow_assignments_workflow_config_id",
        table_name="workflow_assignments",
    )
    op.drop_index(
        "ix_workflow_assignments_tenant_id",
        table_name="workflow_assignments",
    )
    op.drop_table("workflow_assignments")
