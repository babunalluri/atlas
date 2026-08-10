"""Add tenant-scoped team user assignments.

Revision ID: 20260809_0002
Revises: 20260809_0001
"""

import sqlalchemy as sa
from alembic import op

revision = "20260809_0002"
down_revision = "20260809_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "team_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("team_config_id", sa.Uuid(), nullable=False),
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
            ["tenant_id", "team_config_id"],
            ["team_configs.tenant_id", "team_configs.id"],
            ondelete="CASCADE",
            name="fk_team_assignment_tenant_config",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "team_config_id",
            "user_id",
            name="uq_team_assignment_tenant_team_user",
        ),
    )
    op.create_index(
        "ix_team_assignments_tenant_id",
        "team_assignments",
        ["tenant_id"],
    )
    op.create_index(
        "ix_team_assignments_team_config_id",
        "team_assignments",
        ["team_config_id"],
    )
    op.create_index(
        "ix_team_assignments_user_id",
        "team_assignments",
        ["user_id"],
    )
    op.execute("ALTER TABLE team_assignments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE team_assignments FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_team_assignments ON team_assignments
        USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_team_assignments_user_id",
        table_name="team_assignments",
    )
    op.drop_index(
        "ix_team_assignments_team_config_id",
        table_name="team_assignments",
    )
    op.drop_index(
        "ix_team_assignments_tenant_id",
        table_name="team_assignments",
    )
    op.drop_table("team_assignments")
