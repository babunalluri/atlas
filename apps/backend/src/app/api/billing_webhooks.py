"""Razorpay payment webhooks — grant credits after successful checkout."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request

from app.billing.razorpay_client import (
    verify_webhook_signature,
    webhook_event_id,
    webhook_payment_notes,
)
from app.billing.service import BillingService
from app.core.settings import get_settings
from app.db.session import SessionFactory

router = APIRouter(prefix="/api/billing/webhooks", tags=["billing-webhooks"])


@router.post("/razorpay")
async def razorpay_webhook(request: Request) -> dict[str, str]:
    settings = get_settings()
    secret = settings.razorpay_webhook_secret.get_secret_value().strip()
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")
    # Fail closed: missing secret must not credit wallets.
    if not secret or not verify_webhook_signature(body, signature, secret):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    import json

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    event = str(payload.get("event") or "")
    if event not in {
        "payment_link.paid",
        "payment.captured",
        "subscription.charged",
        "subscription.activated",
    }:
        return {"status": "ignored"}

    notes = webhook_payment_notes(payload)
    tenant_raw = notes.get("tenant_id")
    wallet_raw = notes.get("wallet_id")
    if not tenant_raw or not wallet_raw:
        return {"status": "ignored"}

    try:
        tenant_id = uuid.UUID(tenant_raw)
        wallet_id = uuid.UUID(wallet_raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid wallet metadata") from exc

    kind = notes.get("kind", "credit_pack")
    pack_credits = int(notes["pack_credits"]) if notes.get("pack_credits") else None
    plan_id = uuid.UUID(notes["plan_id"]) if notes.get("plan_id") else None
    checkout_id = webhook_event_id(payload)

    async with SessionFactory() as session:
        if session.bind and session.bind.dialect.name == "postgresql":
            from sqlalchemy import text

            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
        session.info["tenant_id"] = tenant_id
        billing = BillingService(session)
        wallet = await billing.fulfill_provider_checkout(
            tenant_id=tenant_id,
            wallet_id=wallet_id,
            kind=kind,
            checkout_id=checkout_id,
            pack_credits=pack_credits,
            plan_id=plan_id,
        )
        await session.commit()
        if wallet is None:
            raise HTTPException(status_code=404, detail="Wallet not found")
    return {"status": "ok"}
