"""Human-readable labels for session/trace actors (staff, guests, API)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import NamedTuple

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.models import Membership, Tenant
from app.db.session import apply_tenant_guc
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


class MembershipProfile(NamedTuple):
    name: str | None
    email: str | None


def _session_dialect(session: AsyncSession) -> str:
    bind = session.bind or session.get_bind()
    if bind is not None:
        return bind.dialect.name
    if get_settings().database_url.startswith("postgresql"):
        return "postgresql"
    return ""


def _profile_from_membership(membership: Membership) -> MembershipProfile | None:
    name = (membership.display_name or "").strip() or None
    email = (membership.email or "").strip() or None
    if not name and not email:
        return None
    return MembershipProfile(name=name, email=email)


async def resolve_membership_profiles(
    session: AsyncSession,
    user_ids: Sequence[str],
) -> dict[str, MembershipProfile]:
    """Resolve display name/email for actors across tenants.

    Platform audit is not tenant-scoped. Postgres RLS still hides memberships
    unless ``app.tenant_id`` is set, so that path walks tenants with a restored
    GUC. SQLite tests have no RLS and search all rows at once.
    """
    unique = sorted({value for value in user_ids if value})
    if not unique:
        return {}

    async def load_visible() -> dict[str, MembershipProfile]:
        rows = await session.scalars(
            select(Membership).where(Membership.user_id.in_(unique))
        )
        found: dict[str, MembershipProfile] = {}
        for membership in rows.all():
            if membership.user_id in found:
                continue
            profile = _profile_from_membership(membership)
            if profile is not None:
                found[membership.user_id] = profile
        return found

    if _session_dialect(session) != "postgresql":
        return await load_visible()

    current = session.info.get("tenant_id")
    tenant_ids = list(await session.scalars(select(Tenant.id)))
    found: dict[str, MembershipProfile] = {}
    try:
        for tenant_id in tenant_ids:
            if len(found) == len(unique):
                break
            await apply_tenant_guc(session, tenant_id)
            for user_id, profile in (await load_visible()).items():
                found.setdefault(user_id, profile)
        return found
    finally:
        if current is not None:
            await apply_tenant_guc(session, current)
        else:
            await session.execute(text("SELECT set_config('app.tenant_id', '', true)"))
            session.info.pop("tenant_id", None)


def label_for(labels: dict[str, str], user_id: str) -> str:
    return labels.get(user_id) or fallback_actor_label(user_id)


def collect_user_ids(items: Iterable[object], attr: str = "user_id") -> list[str]:
    return [str(getattr(item, attr)) for item in items if getattr(item, attr, None)]
