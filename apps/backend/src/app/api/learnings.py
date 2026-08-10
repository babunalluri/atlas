"""Tenant-scoped learnings API wrapping Agno learning stores.

Native AgentOS ``/learnings`` routes are blocked; product traffic uses this
``/api/admin/learnings`` surface with ``runtime_user_id`` namespacing.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import uuid4

from anyio import to_thread
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from app.agent_runtime.persistence import get_agno_db, runtime_user_id
from app.auth.dependencies import require_roles
from app.db.models import Role
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/api/admin/learnings", tags=["admin-learnings"])
AdminContext = Annotated[
    TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
]

IDENTITY_KEYED = frozenset(
    {"user_profile", "user_memory", "entity_memory", "session_context"}
)


class LearningCreateIn(BaseModel):
    learning_type: str = Field(min_length=1, max_length=64)
    content: dict[str, Any] = Field(default_factory=dict)
    namespace: str | None = Field(default=None, max_length=255)
    user_id: str | None = Field(default=None, max_length=255)
    agent_id: str | None = Field(default=None, max_length=255)
    team_id: str | None = Field(default=None, max_length=255)
    session_id: str | None = Field(default=None, max_length=255)
    entity_id: str | None = Field(default=None, max_length=255)
    entity_type: str | None = Field(default=None, max_length=64)
    metadata: dict[str, Any] | None = None


class LearningUpdateIn(BaseModel):
    content: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


def _scoped_user(context: TenantContext, user_id: str | None) -> str | None:
    if user_id is None:
        return None
    return runtime_user_id(context, user_id)


def _serialize(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        data = dict(row)
    elif hasattr(row, "model_dump"):
        data = row.model_dump()
    elif hasattr(row, "to_dict"):
        raw = row.to_dict()
        data = raw if isinstance(raw, dict) else {}
    else:
        data = {}
        for key in (
            "id",
            "learning_id",
            "learning_type",
            "namespace",
            "user_id",
            "agent_id",
            "team_id",
            "session_id",
            "entity_id",
            "entity_type",
            "content",
            "metadata",
            "created_at",
            "updated_at",
        ):
            if hasattr(row, key):
                data[key] = getattr(row, key)
    learning_id = data.get("learning_id") or data.get("id")
    return {
        "learning_id": learning_id,
        "learning_type": data.get("learning_type"),
        "namespace": data.get("namespace"),
        "user_id": data.get("user_id"),
        "agent_id": data.get("agent_id"),
        "team_id": data.get("team_id"),
        "session_id": data.get("session_id"),
        "entity_id": data.get("entity_id"),
        "entity_type": data.get("entity_type"),
        "content": data.get("content") or {},
        "metadata": data.get("metadata") or {},
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }


def _learning_id(
    *,
    learning_type: str,
    user_id: str | None,
    session_id: str | None,
    entity_id: str | None,
    entity_type: str | None,
    namespace: str | None,
) -> str:
    try:
        from agno.learn.utils import build_learning_id

        deterministic = build_learning_id(
            learning_type,
            user_id=user_id,
            session_id=session_id,
            entity_id=entity_id,
            entity_type=entity_type,
            namespace=namespace,
        )
        if deterministic:
            return deterministic
    except Exception:
        pass
    return str(uuid4())


@router.get("")
async def list_learnings(
    context: AdminContext,
    learning_type: str | None = None,
    user_id: str | None = None,
    agent_id: str | None = None,
    team_id: str | None = None,
    session_id: str | None = None,
    namespace: str | None = None,
    entity_id: str | None = None,
    entity_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    page: int = Query(default=1, ge=1),
) -> dict[str, Any]:
    scoped = _scoped_user(context, user_id)
    db = get_agno_db()

    def _list() -> tuple[list[Any], int]:
        return db.list_learnings(
            learning_type=learning_type,
            user_id=scoped,
            agent_id=agent_id,
            team_id=team_id,
            session_id=session_id,
            namespace=namespace,
            entity_id=entity_id,
            entity_type=entity_type,
            include_global=True,
            limit=limit,
            page=page,
            sort_by="updated_at",
            sort_order="desc",
        )

    try:
        rows, total = await to_thread.run_sync(_list)
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=501, detail="Learnings not supported by configured database"
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to list learnings: {exc}") from exc

    return {
        "data": [_serialize(row) for row in rows],
        "meta": {
            "page": page,
            "limit": limit,
            "total_count": total,
            "total_pages": (total + limit - 1) // limit if total else 0,
        },
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_learning(body: LearningCreateIn, context: AdminContext) -> dict[str, Any]:
    scoped_user = _scoped_user(context, body.user_id) if body.user_id else None
    if body.learning_type in IDENTITY_KEYED and not (
        scoped_user or body.session_id or body.entity_id
    ):
        raise HTTPException(
            status_code=422,
            detail="Identity-keyed learnings require user_id, session_id, or entity_id",
        )
    learning_id = _learning_id(
        learning_type=body.learning_type,
        user_id=scoped_user,
        session_id=body.session_id,
        entity_id=body.entity_id,
        entity_type=body.entity_type,
        namespace=body.namespace,
    )
    db = get_agno_db()
    existing = await to_thread.run_sync(lambda: db.get_learning_by_id(learning_id))
    if existing is not None:
        raise HTTPException(status_code=409, detail="Learning already exists; use PATCH")

    def _upsert() -> None:
        db.upsert_learning(
            id=learning_id,
            learning_type=body.learning_type,
            content=body.content,
            user_id=scoped_user,
            agent_id=body.agent_id,
            team_id=body.team_id,
            session_id=body.session_id,
            namespace=body.namespace,
            entity_id=body.entity_id,
            entity_type=body.entity_type,
            metadata=body.metadata,
        )

    try:
        await to_thread.run_sync(_upsert)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create learning: {exc}") from exc

    created = await to_thread.run_sync(lambda: db.get_learning_by_id(learning_id))
    return _serialize(
        created
        or {
            "learning_id": learning_id,
            "learning_type": body.learning_type,
            "content": body.content,
            "user_id": scoped_user,
            "namespace": body.namespace,
            "agent_id": body.agent_id,
            "team_id": body.team_id,
            "session_id": body.session_id,
            "entity_id": body.entity_id,
            "entity_type": body.entity_type,
            "metadata": body.metadata or {},
        }
    )


@router.get("/{learning_id}")
async def get_learning(learning_id: str, context: AdminContext) -> dict[str, Any]:
    row = await to_thread.run_sync(lambda: get_agno_db().get_learning_by_id(learning_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Learning not found")
    return _serialize(row)


@router.patch("/{learning_id}")
async def update_learning(
    learning_id: str, body: LearningUpdateIn, context: AdminContext
) -> dict[str, Any]:
    db = get_agno_db()
    existing = await to_thread.run_sync(lambda: db.get_learning_by_id(learning_id))
    if existing is None:
        raise HTTPException(status_code=404, detail="Learning not found")
    content = body.content
    if content is None:
        content = (
            existing.get("content")
            if isinstance(existing, dict)
            else getattr(existing, "content", None)
        ) or {}
    updated = await to_thread.run_sync(
        lambda: db.update_learning(learning_id, content=content, metadata=body.metadata)
    )
    if updated is False:
        raise HTTPException(status_code=404, detail="Learning not found")
    row = await to_thread.run_sync(lambda: db.get_learning_by_id(learning_id))
    return _serialize(row or existing)


@router.delete("/{learning_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_learning(learning_id: str, context: AdminContext) -> Response:
    deleted = await to_thread.run_sync(lambda: get_agno_db().delete_learning(learning_id))
    if not deleted:
        raise HTTPException(status_code=404, detail="Learning not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
