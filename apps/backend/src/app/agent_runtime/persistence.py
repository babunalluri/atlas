"""Shared AgentOS persistence and tenant-safe runtime identifiers."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.core.settings import get_settings
from app.tenancy.context import TenantContext


@lru_cache
def get_agno_db() -> Any:
    """Return the same durable Postgres provider used by AgentOS and custom runs."""
    from agno.db.postgres import PostgresDb

    settings = get_settings()
    return PostgresDb(
        db_url=settings.agno_database_url,
        session_table="agno_sessions",
        memory_table="agno_memories",
        traces_table="agno_traces",
        spans_table="agno_spans",
        approvals_table="agno_approvals",
    )


def runtime_user_id(context: TenantContext, user_id: str | None = None) -> str:
    """Namespace the authoritative Clerk subject without accepting client tenancy."""
    return f"tenant:{context.tenant_id}:user:{user_id or context.user_id}"


def runtime_session_id(context: TenantContext, external_session_id: str) -> str:
    """Namespace a product session before it reaches AgentOS storage."""
    return f"tenant:{context.tenant_id}:session:{external_session_id}"
