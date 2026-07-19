"""enforce composite tenant foreign keys on runtime links

Revision ID: 20260719_0006
Revises: 20260719_0005
Create Date: 2026-07-19
"""

from typing import Sequence, Union

from alembic import op

revision: str = "20260719_0006"
down_revision: Union[str, None] = "20260719_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_conversation_session_tenant_id",
        "conversation_sessions",
        ["tenant_id", "id"],
    )
    op.create_foreign_key(
        "fk_conversation_agent_version",
        "conversation_sessions",
        "agent_versions",
        ["tenant_id", "agent_config_id", "agent_version_id"],
        ["tenant_id", "agent_config_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_conversation_team_version",
        "conversation_sessions",
        "team_versions",
        ["tenant_id", "team_config_id", "team_version_id"],
        ["tenant_id", "team_config_id", "id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint("approval_bindings_session_id_fkey", "approval_bindings", type_="foreignkey")
    op.create_foreign_key(
        "fk_approval_tenant_session",
        "approval_bindings",
        "conversation_sessions",
        ["tenant_id", "session_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_approval_tenant_session", "approval_bindings", type_="foreignkey")
    op.create_foreign_key(
        "approval_bindings_session_id_fkey",
        "approval_bindings",
        "conversation_sessions",
        ["session_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("fk_conversation_team_version", "conversation_sessions", type_="foreignkey")
    op.drop_constraint("fk_conversation_agent_version", "conversation_sessions", type_="foreignkey")
    op.drop_constraint(
        "uq_conversation_session_tenant_id",
        "conversation_sessions",
        type_="unique",
    )
