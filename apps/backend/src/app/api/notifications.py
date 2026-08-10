"""Org-admin in-app notifications + recipient inbox APIs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.db.models import Role
from app.db.repositories import MembershipRepository, UserNotificationRepository
from app.db.session import tenant_session
from app.tenancy.context import TenantContext

admin_router = APIRouter(prefix="/admin/notifications", tags=["admin-notifications"])
me_router = APIRouter(prefix="/api/me/notifications", tags=["user-notifications"])

AdminContext = Annotated[
    TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
]
MeContext = Annotated[
    TenantContext,
    Depends(require_roles(Role.platform_admin, Role.tenant_admin, Role.end_user)),
]
TenantSession = Annotated[AsyncSession, Depends(tenant_session)]


class NotificationCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)
    user_id: str | None = Field(
        default=None,
        max_length=255,
        description="Target one user; omit or null to notify all active members.",
    )

    @field_validator("title", "body")
    @classmethod
    def strip_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Value is required")
        return cleaned

    @field_validator("user_id")
    @classmethod
    def strip_user_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class NotificationOut(BaseModel):
    id: uuid.UUID
    batch_id: uuid.UUID
    title: str
    body: str
    audience: Literal["user", "all"]
    created_by: str
    read_at: datetime | None
    created_at: datetime


class NotificationBatchOut(BaseModel):
    batch_id: uuid.UUID
    title: str
    body: str
    audience: Literal["user", "all"]
    created_by: str
    recipient_count: int
    created_at: datetime


class NotificationSendOut(BaseModel):
    batch_id: uuid.UUID
    audience: Literal["user", "all"]
    recipient_count: int
    title: str


class UnreadCountOut(BaseModel):
    count: int


class MarkAllReadOut(BaseModel):
    updated: int


@admin_router.post("", response_model=NotificationSendOut)
async def send_notification(
    body: NotificationCreateIn,
    context: AdminContext,
    session: TenantSession,
) -> NotificationSendOut:
    memberships = MembershipRepository(session, context)
    notifications = UserNotificationRepository(session, context)

    if body.user_id is not None:
        membership = await memberships.get_by_user_id(body.user_id)
        if membership is None or not membership.is_active:
            raise HTTPException(
                status_code=404, detail="Active user not found in this organization"
            )
        if membership.user_id.startswith("invite:"):
            raise HTTPException(
                status_code=400, detail="Cannot notify a pending invite account"
            )
        recipients = [membership.user_id]
        audience: Literal["user", "all"] = "user"
    else:
        rows = await memberships.list_users()
        recipients = [
            row.user_id
            for row in rows
            if row.is_active and row.user_id and not row.user_id.startswith("invite:")
        ]
        audience = "all"

    try:
        batch_id, created = await notifications.create_batch(
            title=body.title,
            body=body.body,
            created_by=context.user_id,
            audience=audience,
            recipient_user_ids=recipients,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return NotificationSendOut(
        batch_id=batch_id,
        audience=audience,
        recipient_count=len(created),
        title=body.title,
    )


@admin_router.get("", response_model=list[NotificationBatchOut])
async def list_sent_notifications(
    context: AdminContext,
    session: TenantSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 40,
) -> list[NotificationBatchOut]:
    rows = await UserNotificationRepository(session, context).list_sent_batches(
        limit=limit
    )
    return [
        NotificationBatchOut(
            batch_id=row["batch_id"],
            title=row["title"],
            body=row["body"],
            audience=row["audience"],
            created_by=row["created_by"],
            recipient_count=row["recipient_count"],
            created_at=row["created_at"],
        )
        for row in rows
    ]


@me_router.get("", response_model=list[NotificationOut])
async def list_my_notifications(
    context: MeContext,
    session: TenantSession,
    unread_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[NotificationOut]:
    rows = await UserNotificationRepository(session, context).list_for_user(
        context.user_id, unread_only=unread_only, limit=limit
    )
    return [
        NotificationOut(
            id=row.id,
            batch_id=row.batch_id,
            title=row.title,
            body=row.body,
            audience=row.audience,  # type: ignore[arg-type]
            created_by=row.created_by,
            read_at=row.read_at,
            created_at=row.created_at,
        )
        for row in rows
    ]


@me_router.get("/unread-count", response_model=UnreadCountOut)
async def my_unread_count(
    context: MeContext,
    session: TenantSession,
) -> UnreadCountOut:
    count = await UserNotificationRepository(session, context).unread_count(
        context.user_id
    )
    return UnreadCountOut(count=count)


@me_router.post("/read-all", response_model=MarkAllReadOut)
async def mark_all_notifications_read(
    context: MeContext,
    session: TenantSession,
) -> MarkAllReadOut:
    updated = await UserNotificationRepository(session, context).mark_all_read(
        context.user_id
    )
    return MarkAllReadOut(updated=updated)


@me_router.post("/{notification_id}/read", response_model=NotificationOut)
async def mark_notification_read(
    notification_id: uuid.UUID,
    context: MeContext,
    session: TenantSession,
) -> NotificationOut:
    row = await UserNotificationRepository(session, context).mark_read(
        context.user_id, notification_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return NotificationOut(
        id=row.id,
        batch_id=row.batch_id,
        title=row.title,
        body=row.body,
        audience=row.audience,  # type: ignore[arg-type]
        created_by=row.created_by,
        read_at=row.read_at,
        created_at=row.created_at,
    )
