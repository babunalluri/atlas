"""Admin CRUD for Slack / Telegram / WhatsApp channel bindings."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ChannelBindingIn, ChannelBindingOut, ChannelBindingUpdateIn
from app.auth.dependencies import require_roles
from app.db.models import Role
from app.db.repositories import (
    ChannelBindingRepository,
    TeamRepository,
    WorkflowRepository,
)
from app.db.session import tenant_session
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/channels", tags=["admin-channels"])
AdminContext = Annotated[
    TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
]


def _out(row) -> ChannelBindingOut:  # type: ignore[no-untyped-def]
    return ChannelBindingOut(
        id=row.id,
        provider=row.provider,
        credential_id=row.credential_id,
        target_type=row.target_type,
        target_config_id=row.target_config_id,
        external_config=dict(row.external_config or {}),
        active=bool(row.active),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _validate_target(
    session: AsyncSession,
    context: TenantContext,
    *,
    target_type: str,
    target_config_id: uuid.UUID,
) -> None:
    if target_type == "team":
        config = await TeamRepository(session, context).get_config(target_config_id)
        if config is None:
            raise HTTPException(status_code=404, detail="Team not found")
        if config.published_version_id is None:
            raise HTTPException(status_code=400, detail="Team must be published")
        return
    if target_type == "workflow":
        config = await WorkflowRepository(session, context).get_config(target_config_id)
        if config is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        if config.published_version_id is None:
            raise HTTPException(status_code=400, detail="Workflow must be published")
        return
    raise HTTPException(status_code=400, detail="target_type must be team or workflow")


@router.get("", response_model=list[ChannelBindingOut])
async def list_bindings(
    context: AdminContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> list[ChannelBindingOut]:
    rows = await ChannelBindingRepository(session, context).list()
    return [_out(row) for row in rows]


@router.post("", response_model=ChannelBindingOut, status_code=201)
async def create_binding(
    body: ChannelBindingIn,
    context: AdminContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> ChannelBindingOut:
    await _validate_target(
        session,
        context,
        target_type=body.target_type,
        target_config_id=body.target_config_id,
    )
    try:
        row = await ChannelBindingRepository(session, context).create(
            provider=body.provider,
            credential_id=body.credential_id,
            target_type=body.target_type,
            target_config_id=body.target_config_id,
            external_config=body.external_config,
            active=body.active,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _out(row)


@router.patch("/{binding_id}", response_model=ChannelBindingOut)
async def update_binding(
    binding_id: uuid.UUID,
    body: ChannelBindingUpdateIn,
    context: AdminContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> ChannelBindingOut:
    repo = ChannelBindingRepository(session, context)
    existing = await repo.get(binding_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Channel binding not found")
    target_type = body.target_type or existing.target_type
    target_config_id = body.target_config_id or existing.target_config_id
    if body.target_type is not None or body.target_config_id is not None:
        await _validate_target(
            session,
            context,
            target_type=target_type,
            target_config_id=target_config_id,
        )
    try:
        row = await repo.update(
            binding_id,
            credential_id=body.credential_id,
            target_type=body.target_type,
            target_config_id=body.target_config_id,
            external_config=body.external_config,
            active=body.active,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    assert row is not None
    return _out(row)


@router.delete("/{binding_id}", status_code=204)
async def delete_binding(
    binding_id: uuid.UUID,
    context: AdminContext,
    session: Annotated[AsyncSession, Depends(tenant_session)],
) -> Response:
    deleted = await ChannelBindingRepository(session, context).delete(binding_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Channel binding not found")
    return Response(status_code=204)
