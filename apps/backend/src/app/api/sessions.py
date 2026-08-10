from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from anyio import to_thread
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.persistence import get_agno_db, runtime_user_id
from app.api.actor_labels import label_for, resolve_actor_labels
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
    data: dict[str, Any] = {}
    for key in (
        "memory_id",
        "id",
        "memory",
        "topics",
        "user_id",
        "input",
        "created_at",
        "updated_at",
        "feedback",
        "agent_id",
        "team_id",
    ):
        if hasattr(value, key):
            data[key] = getattr(value, key)
    return data


def _session_out(row: Any, *, user_label: str | None = None) -> dict[str, Any]:
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
        "user_label": user_label,
        "last_run_id": row.last_run_id,
        "status": row.status,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _assert_memory_user(context: TenantContext, user_id: str | None) -> str:
    if user_id and user_id != context.user_id and not context.can_administer():
        raise HTTPException(status_code=403, detail="Administrator role required")
    return runtime_user_id(context, user_id)


class MemoryUpsertIn(BaseModel):
    memory: str = Field(min_length=1, max_length=20_000)
    topics: list[str] = Field(default_factory=list, max_length=32)
    user_id: str | None = Field(default=None, max_length=255)


class MemoryOptimizeIn(BaseModel):
    user_id: str | None = Field(default=None, max_length=255)
    apply: bool = True
    model: str | None = Field(default=None, max_length=255)


@router.get("/sessions")
async def list_sessions(
    context: Annotated[TenantContext, Depends(require_tenant)],
    session: Annotated[AsyncSession, Depends(tenant_session)],
    target_type: Literal["agent", "team", "workflow"] | None = None,
    target_id: uuid.UUID | None = None,
    all_users: bool = Query(False),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    rows = await SessionRepository(session, context).list_for_user(
        target_type=target_type,
        target_id=target_id,
        include_all_users=all_users,
        limit=limit,
    )
    labels = await resolve_actor_labels(
        session, context, [row.user_id for row in rows]
    )
    return [
        _session_out(row, user_label=label_for(labels, row.user_id)) for row in rows
    ]


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
    labels = await resolve_actor_labels(session, context, [row.user_id])
    result = _session_out(row, user_label=label_for(labels, row.user_id))
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
    scoped_user = _assert_memory_user(context, user_id)
    rows = await to_thread.run_sync(
        lambda: get_agno_db().get_user_memories(user_id=scoped_user, limit=100)
    )
    return [_serialize(row) for row in rows]


@router.post("/memories", status_code=201)
async def create_memory(
    body: MemoryUpsertIn,
    context: Annotated[TenantContext, Depends(require_tenant)],
) -> dict[str, Any]:
    from agno.db.schemas import UserMemory

    scoped_user = _assert_memory_user(context, body.user_id)
    memory = UserMemory(
        memory=body.memory,
        topics=body.topics or None,
        user_id=scoped_user,
    )
    saved = await to_thread.run_sync(lambda: get_agno_db().upsert_user_memory(memory))
    return _serialize(saved)


@router.patch("/memories/{memory_id}")
async def update_memory(
    memory_id: str,
    body: MemoryUpsertIn,
    context: Annotated[TenantContext, Depends(require_tenant)],
) -> dict[str, Any]:
    from agno.db.schemas import UserMemory

    scoped_user = _assert_memory_user(context, body.user_id)
    existing = await to_thread.run_sync(
        lambda: get_agno_db().get_user_memory(memory_id, user_id=scoped_user)
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    memory = UserMemory(
        memory_id=memory_id,
        memory=body.memory,
        topics=body.topics or None,
        user_id=scoped_user,
    )
    saved = await to_thread.run_sync(lambda: get_agno_db().upsert_user_memory(memory))
    return _serialize(saved)


@router.post("/memories/optimize")
async def optimize_memories(
    body: MemoryOptimizeIn,
    context: Annotated[TenantContext, Depends(require_tenant)],
) -> dict[str, Any]:
    from agno.memory import MemoryManager
    from agno.memory.strategies.types import MemoryOptimizationStrategyType

    scoped_user = _assert_memory_user(context, body.user_id)
    db = get_agno_db()
    before = await to_thread.run_sync(
        lambda: db.get_user_memories(user_id=scoped_user, limit=500)
    )
    if not before:
        raise HTTPException(status_code=404, detail="No memories found for user")

    manager_kwargs: dict[str, Any] = {"db": db}
    if body.model:
        from agno.models.utils import get_model

        try:
            manager_kwargs["model"] = get_model(body.model)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    manager = MemoryManager(**manager_kwargs)
    optimized = await to_thread.run_sync(
        lambda: manager.optimize_memories(
            user_id=scoped_user,
            strategy=MemoryOptimizationStrategyType.SUMMARIZE,
            apply=body.apply,
        )
    )
    return {
        "user_id": body.user_id or context.user_id,
        "memories_before": len(before),
        "memories_after": len(optimized or []),
        "memories": [_serialize(row) for row in (optimized or [])],
        "applied": body.apply,
    }


@router.delete("/memories/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: str,
    context: Annotated[TenantContext, Depends(require_tenant)],
    user_id: str | None = None,
) -> Response:
    scoped_user = _assert_memory_user(context, user_id)
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
