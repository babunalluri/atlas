"""Human-readable labels for session/trace actors (staff, guests, API)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Membership
from app.tenancy.context import TenantContext


def fallback_actor_label(user_id: str) -> str:
    if not user_id:
        return "Unknown"
    if user_id.startswith("guest:"):
        rest = user_id.removeprefix("guest:")
        if rest.startswith("ip:"):
            return "Guest (anonymous)"
        return f"Guest · {rest[:8]}"
    if user_id.startswith("sa:"):
        return f"API · {user_id[3:11]}"
    if user_id.startswith("user_") and len(user_id) > 16:
        return f"User · {user_id[5:13]}…"
    if user_id.startswith("public-") or user_id == "public-surface":
        return "Public"
    return user_id


def membership_actor_label(
    *,
    user_id: str,
    display_name: str | None,
    email: str | None,
) -> str:
    name = (display_name or "").strip()
    if name:
        return name
    mail = (email or "").strip()
    if mail:
        return mail
    return fallback_actor_label(user_id)


async def resolve_actor_labels(
    session: AsyncSession,
    context: TenantContext,
    user_ids: Sequence[str],
) -> dict[str, str]:
    """Map raw user ids → display labels using tenant memberships."""
    unique = sorted({value for value in user_ids if value})
    if not unique:
        return {}
    rows = await session.scalars(
        select(Membership).where(
            Membership.tenant_id == context.tenant_id,
            Membership.user_id.in_(unique),
        )
    )
    labels: dict[str, str] = {}
    for membership in rows.all():
        labels[membership.user_id] = membership_actor_label(
            user_id=membership.user_id,
            display_name=membership.display_name,
            email=membership.email,
        )
    for user_id in unique:
        labels.setdefault(user_id, fallback_actor_label(user_id))
    return labels


def label_for(labels: dict[str, str], user_id: str) -> str:
    return labels.get(user_id) or fallback_actor_label(user_id)


def collect_user_ids(items: Iterable[object], attr: str = "user_id") -> list[str]:
    return [str(getattr(item, attr)) for item in items if getattr(item, attr, None)]
