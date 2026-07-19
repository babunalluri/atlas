"""extensible tool providers

Revision ID: 20260719_0004
Revises: 20260719_0003
Create Date: 2026-07-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0004"
down_revision: Union[str, None] = "20260719_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tool_definitions",
        sa.Column(
            "connection_status",
            sa.String(32),
            nullable=False,
            server_default="unvalidated",
        ),
    )
    op.add_column(
        "tool_definitions",
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tool_definitions",
        sa.Column("last_validation_error", sa.String(500), nullable=True),
    )

    # Preserve 0003 rows while moving their HTTP-specific fields into the
    # provider config document used by the new registry.
    op.execute(
        """
        UPDATE tool_definitions
        SET config = jsonb_strip_nulls(
            jsonb_build_object(
                'base_url', base_url,
                'method', http_method,
                'path', path,
                'request_schema', request_schema,
                'response_description', response_description,
                'response_schema', response_schema,
                'headers', headers,
                'credential_header', COALESCE(config::jsonb->>'credential_header', 'Authorization'),
                'credential_prefix', COALESCE(config::jsonb->>'credential_prefix', 'Bearer '),
                'timeout_seconds', COALESCE((config::jsonb->>'timeout_seconds')::numeric, 10)
            )
        ),
        kind = 'http'
        WHERE kind IN ('rest', 'webhook')
        """
    )
    op.alter_column("tool_definitions", "http_method", existing_type=sa.String(10), nullable=True)
    op.alter_column("tool_definitions", "base_url", existing_type=sa.Text(), nullable=True)
    op.alter_column("tool_definitions", "path", existing_type=sa.Text(), nullable=True)
    op.create_check_constraint(
        "ck_tool_definition_provider",
        "tool_definitions",
        "kind IN ('http', 'openapi', 'python_toolkit', 'mcp')",
    )
    op.create_index(
        "ix_tool_definitions_tenant_kind_active",
        "tool_definitions",
        ["tenant_id", "kind", "active"],
    )


def downgrade() -> None:
    # Downgrade cannot represent non-HTTP providers in the 0003 schema.
    op.execute("DELETE FROM tool_definitions WHERE kind <> 'http'")
    op.execute(
        """
        UPDATE tool_definitions
        SET kind = 'rest',
            http_method = config::jsonb->>'method',
            base_url = config::jsonb->>'base_url',
            path = COALESCE(config::jsonb->>'path', ''),
            request_schema = COALESCE(config::jsonb->'request_schema', '{}'::jsonb),
            response_description = config::jsonb->>'response_description',
            response_schema = config::jsonb->'response_schema',
            headers = COALESCE(config::jsonb->'headers', '{}'::jsonb),
            config = jsonb_strip_nulls(
                jsonb_build_object(
                    'credential_header', config::jsonb->>'credential_header',
                    'credential_prefix', config::jsonb->>'credential_prefix',
                    'timeout_seconds', config::jsonb->'timeout_seconds'
                )
            )
        """
    )
    op.drop_index("ix_tool_definitions_tenant_kind_active", table_name="tool_definitions")
    op.drop_constraint("ck_tool_definition_provider", "tool_definitions", type_="check")
    op.alter_column("tool_definitions", "path", existing_type=sa.Text(), nullable=False)
    op.alter_column("tool_definitions", "base_url", existing_type=sa.Text(), nullable=False)
    op.alter_column("tool_definitions", "http_method", existing_type=sa.String(10), nullable=False)
    op.drop_column("tool_definitions", "last_validation_error")
    op.drop_column("tool_definitions", "last_validated_at")
    op.drop_column("tool_definitions", "connection_status")
