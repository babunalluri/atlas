"""Tenant end-user identity for public chat/email.

Revision ID: 20260809_0001
Revises: 20260719_0017
"""

import sqlalchemy as sa
from alembic import op

revision = "20260809_0001"
down_revision = "20260719_0017"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
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
        "end_users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "metadata",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
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
        sa.UniqueConstraint("tenant_id", "email", name="uq_end_user_tenant_email"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_end_user_tenant_id"),
    )
    op.create_index("ix_end_users_tenant_id", "end_users", ["tenant_id"])
    op.create_index("ix_end_users_tenant_email", "end_users", ["tenant_id", "email"])
    _rls("end_users")

    op.create_table(
        "verification_challenges",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("code_hash", sa.String(128), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False, server_default="bind_session"),
        sa.Column("external_session_id", sa.String(255), nullable=False),
        sa.Column("guest_user_id", sa.String(255), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("tenant_id", "id", name="uq_verification_challenge_tenant_id"),
    )
    op.create_index(
        "ix_verification_challenges_tenant_id",
        "verification_challenges",
        ["tenant_id"],
    )
    op.create_index(
        "ix_verification_challenges_lookup",
        "verification_challenges",
        ["tenant_id", "external_session_id", "email"],
    )
    _rls("verification_challenges")

    op.add_column(
        "conversation_sessions",
        sa.Column("verified_end_user_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_conversation_verified_end_user",
        "conversation_sessions",
        "end_users",
        ["tenant_id", "verified_end_user_id"],
        ["tenant_id", "id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_conversation_sessions_verified_end_user",
        "conversation_sessions",
        ["tenant_id", "verified_end_user_id"],
    )

    op.create_table(
        "end_user_session_binds",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_session_id", sa.String(255), nullable=False),
        sa.Column("guest_user_id", sa.String(255), nullable=False),
        sa.Column("end_user_id", sa.Uuid(), nullable=False),
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
            ["tenant_id", "end_user_id"],
            ["end_users.tenant_id", "end_users.id"],
            ondelete="CASCADE",
            name="fk_end_user_session_bind_user",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "external_session_id",
            name="uq_end_user_session_bind_session",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_end_user_session_bind_tenant_id"),
    )
    op.create_index(
        "ix_end_user_session_binds_tenant_id",
        "end_user_session_binds",
        ["tenant_id"],
    )
    _rls("end_user_session_binds")


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_end_user_session_binds "
        "ON end_user_session_binds"
    )
    op.drop_index("ix_end_user_session_binds_tenant_id", table_name="end_user_session_binds")
    op.drop_table("end_user_session_binds")

    op.drop_index(
        "ix_conversation_sessions_verified_end_user",
        table_name="conversation_sessions",
    )
    op.drop_constraint(
        "fk_conversation_verified_end_user",
        "conversation_sessions",
        type_="foreignkey",
    )
    op.drop_column("conversation_sessions", "verified_end_user_id")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_verification_challenges ON verification_challenges")
    op.drop_index("ix_verification_challenges_lookup", table_name="verification_challenges")
    op.drop_index("ix_verification_challenges_tenant_id", table_name="verification_challenges")
    op.drop_table("verification_challenges")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_end_users ON end_users")
    op.drop_index("ix_end_users_tenant_email", table_name="end_users")
    op.drop_index("ix_end_users_tenant_id", table_name="end_users")
    op.drop_table("end_users")
