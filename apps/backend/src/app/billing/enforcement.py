"""HTTP-facing billing guards for agent runs."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.service import BillingError, BillingService
from app.tenancy.context import TenantContext


async def require_credits_for_run(
    session: AsyncSession,
    context: TenantContext,
    *,
    preview: bool = False,
    scheduler: bool = False,
) -> None:
    billing = BillingService(session, context)
    try:
        await billing.assert_can_run(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            admin_preview=preview,
            scheduler=scheduler,
        )
    except BillingError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc


async def record_run_billing(
    session: AsyncSession,
    context: TenantContext,
    payload: dict[str, Any],
    *,
    preview: bool = False,
    scheduler: bool = False,
) -> None:
    if str(payload.get("event") or "") != "RunCompleted":
        return
    billing = BillingService(session, context)
    run_id = payload.get("run_id")
    await billing.record_run_usage(
        tenant_id=context.tenant_id,
        user_id=context.user_id,
        run_id=str(run_id) if run_id else None,
        payload=payload,
        admin_preview=preview,
        scheduler=scheduler,
    )


async def record_output_billing(
    session: AsyncSession,
    context: TenantContext,
    output: dict[str, Any],
    *,
    scheduler: bool = True,
) -> None:
    billing = BillingService(session, context)
    await billing.record_run_usage(
        tenant_id=context.tenant_id,
        user_id=context.user_id,
        run_id=str(output.get("run_id")) if output.get("run_id") else None,
        payload=output,
        scheduler=scheduler,
    )
