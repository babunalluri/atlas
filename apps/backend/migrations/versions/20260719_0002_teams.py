"""tenant-scoped immutable team versions

Revision ID: 20260719_0002
Revises: 20260719_0001
Create Date: 2026-07-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260719_0002"
down_revision: Union[str, None] = "20260719_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TEAM_TABLES = ["team_configs", "team_versions", "team_members"]


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


def upgrade() -> None:
    op.create_unique_constraint("uq_agent_config_tenant_id", "agent_configs", ["tenant_id", "id"])
    op.create_unique_constraint(
        "uq_agent_version_tenant_config_id",
        "agent_versions",
        ["tenant_id", "agent_config_id", "id"],
    )

    op.create_table(
        "team_configs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("published_version_id", sa.Uuid()),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_team_tenant_slug"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_team_config_tenant_id"),
    )
    op.create_index("ix_team_configs_tenant_id", "team_configs", ["tenant_id"])

    op.create_table(
        "team_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("team_config_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "draft", "published", "archived", name="agentstatus", create_type=False
            ),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False, server_default="coordinate"),
        sa.Column("model_id", sa.String(255), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0.2"),
        sa.Column("created_by", sa.String(255), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "team_config_id"],
            ["team_configs.tenant_id", "team_configs.id"],
            ondelete="CASCADE",
            name="fk_team_version_tenant_config",
        ),
        sa.UniqueConstraint("tenant_id", "team_config_id", "version", name="uq_team_version"),
        sa.UniqueConstraint(
            "tenant_id", "team_config_id", "id", name="uq_team_version_tenant_config_id"
        ),
    )
    op.create_index("ix_team_versions_tenant_id", "team_versions", ["tenant_id"])
    op.create_index("ix_team_versions_team_config_id", "team_versions", ["team_config_id"])

    op.create_table(
        "team_members",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("team_config_id", sa.Uuid(), nullable=False),
        sa.Column("team_version_id", sa.Uuid(), nullable=False),
        sa.Column("agent_config_id", sa.Uuid(), nullable=False),
        sa.Column("agent_version_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "team_config_id", "team_version_id"],
            ["team_versions.tenant_id", "team_versions.team_config_id", "team_versions.id"],
            ondelete="CASCADE",
            name="fk_team_member_tenant_team_version",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "agent_config_id", "agent_version_id"],
            ["agent_versions.tenant_id", "agent_versions.agent_config_id", "agent_versions.id"],
            ondelete="RESTRICT",
            name="fk_team_member_tenant_agent_version",
        ),
    )
    op.create_index("ix_team_members_tenant_id", "team_members", ["tenant_id"])
    op.create_index("ix_team_members_team_version_id", "team_members", ["team_version_id"])
    op.create_index(
        "uq_team_member_position", "team_members", ["team_version_id", "position"], unique=True
    )
    op.create_index(
        "ix_team_members_tenant_agent", "team_members", ["tenant_id", "agent_config_id"]
    )

    for table in TEAM_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
            USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            """
        )


def downgrade() -> None:
    for table in reversed(TEAM_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.drop_table(table)
    op.drop_constraint("uq_agent_version_tenant_config_id", "agent_versions", type_="unique")
    op.drop_constraint("uq_agent_config_tenant_id", "agent_configs", type_="unique")
