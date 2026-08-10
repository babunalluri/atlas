"""Identity tools bound to the verified end user (never trust model-supplied ids)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import EndUserRepository
from app.tenancy.context import TenantContext, current_tenant_or_none
from app.tools.registry import tool


def build_identity_tools(session: AsyncSession, context: TenantContext) -> list[Any]:
    """Return tools that read/update only the verified end user on this context."""

    @tool(name="my_profile")
    async def my_profile() -> str:
        """Return the verified customer's profile for this chat session.

        Only works after the customer verifies their email. Never accepts a user id
        from the model — identity comes from the session bind.
        """
        ctx = current_tenant_or_none() or context
        if ctx.verified_end_user_id is None:
            return json.dumps(
                {
                    "verified": False,
                    "message": "Customer is not verified. Ask them to verify their email.",
                }
            )
        user = await EndUserRepository(session, ctx).get(ctx.verified_end_user_id)
        if user is None or not user.is_active:
            return json.dumps({"verified": False, "message": "Verified user not found"})
        return json.dumps(
            {
                "verified": True,
                "id": str(user.id),
                "email": user.email,
                "display_name": user.display_name,
                "metadata": user.user_metadata or {},
            }
        )

    @tool(name="update_my_profile")
    async def update_my_profile(display_name: str = "") -> str:
        """Update the verified customer's display name.

        Ignores any user id the model invents; only the session-bound user is updated.
        """
        ctx = current_tenant_or_none() or context
        if ctx.verified_end_user_id is None:
            return json.dumps(
                {
                    "ok": False,
                    "message": "Customer is not verified.",
                }
            )
        user = await EndUserRepository(session, ctx).update_profile(
            ctx.verified_end_user_id,
            display_name=display_name or None,
        )
        if user is None:
            return json.dumps({"ok": False, "message": "Verified user not found"})
        return json.dumps(
            {
                "ok": True,
                "id": str(user.id),
                "email": user.email,
                "display_name": user.display_name,
            }
        )

    return [my_profile, update_my_profile]


def attach_identity_tools(runtime: Any, session: AsyncSession, context: TenantContext) -> None:
    tools = build_identity_tools(session, context)
    existing = list(getattr(runtime, "tools", None) or [])
    existing.extend(tools)
    try:
        runtime.tools = existing
    except Exception:
        if hasattr(runtime, "set_tools"):
            runtime.set_tools(existing)
