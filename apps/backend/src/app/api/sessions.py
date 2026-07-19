from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from anyio import to_thread
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.persistence import get_agno_db, runtime_user_id
from app.auth.dependencies import require_tenant
from app.db.repositories import SessionRepository
from app.db.session import tenant_session
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/api", tags=["sessions-memory"])


def _serialize(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        result = value.to_dict()
        return result if isinstance(result, dict) else {}
    if isinstance(value, dict):
        return value
    return {}


def _session_out(row: Any) -> dict[str, Any]:
    return {
        "id": row.external_session_id,
        "title": row.title or "New conversation",
        "target_type": row.target_type,
        "agent_config_id": str(row.agent_config_id) if row.agent_config_id else None,
        "agent_version_id": str(row.agent_version_id) if row.agent_version_id else None,
        "team_config_id": str(row.team_config_id) if row.team_config_id else None,
        "team_version_id": str(row.team_version_id) if row.team_version_id else None,
        "workflow_config_id": str(row.workflow_config_id) if row.workflow_config_id else None,
        "workflow_version_id": str(row.workflow_version_id) if row.workflow_version_id else None,
        "user_id": row.user_id,
        "last_run_id": row.last_run_id,
        "status": row.status,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.get("/sessions")
async def list_sessions(
    context: Annotated[TenantContext, Depends(require_tenant)],
    session: Annotated[AsyncSession, Depends(tenant_session)],
    target_type: Literal["agent", "team", "workflow"] | None = None,
    target_id: uuid.UUID | None = None,
    all_users: bool = Query(False),
) -> list[dict[str, Any]]:
    rows = await SessionRepository(session, context).list_for_user(
        target_type=target_type,
        target_id=target_id,
        include_all_users=all_users,
    )
    return [_session_out(row) for row in rows]


@router.get("/sessions/{external_session_id}")
async def get_session(
    external_session_id: str,
    context: Annotated[TenantContext, Depends(require_tenant)],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> dict[str, Any]:
    row = await SessionRepository(session, context).get_accessible(external_session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    native = await to_thread.run_sync(
        lambda: get_agno_db().get_session(
            row.runtime_session_id,
            user_id=row.runtime_user_id,
        )
    )
    result = _session_out(row)
    result["history"] = _serialize(native) if native is not None else {"runs": []}
    return result


@router.delete("/sessions/{external_session_id}", status_code=204)
async def delete_session(
    external_session_id: str,
    context: Annotated[TenantContext, Depends(require_tenant)],
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> Response:
    repo = SessionRepository(session, context)
    row = await repo.get_accessible(external_session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    await to_thread.run_sync(
        lambda: get_agno_db().delete_session(
            row.runtime_session_id,
            user_id=row.runtime_user_id,
        )
    )
    await repo.delete(external_session_id)
    return Response(status_code=204)


@router.get("/memories")
async def list_memories(
    context: Annotated[TenantContext, Depends(require_tenant)],
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    if user_id and user_id != context.user_id and not context.can_administer():
        raise HTTPException(status_code=403, detail="Administrator role required")
    scoped_user = runtime_user_id(context, user_id)
    rows = await to_thread.run_sync(
        lambda: get_agno_db().get_user_memories(user_id=scoped_user, limit=100)
    )
    return [_serialize(row) for row in rows]


@router.delete("/memories/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: str,
    context: Annotated[TenantContext, Depends(require_tenant)],
    user_id: str | None = None,
) -> Response:
    if user_id and user_id != context.user_id and not context.can_administer():
        raise HTTPException(status_code=403, detail="Administrator role required")
    scoped_user = runtime_user_id(context, user_id)
    rows = await to_thread.run_sync(
        lambda: get_agno_db().get_user_memories(user_id=scoped_user, limit=100)
    )
    if not any(
        str(_serialize(row).get("memory_id") or _serialize(row).get("id")) == memory_id
        for row in rows
    ):
        raise HTTPException(status_code=404, detail="Memory not found")
    await to_thread.run_sync(
        lambda: get_agno_db().delete_user_memory(memory_id, user_id=scoped_user)
    )
    return Response(status_code=204)
