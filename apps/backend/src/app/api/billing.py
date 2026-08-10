"""Tenant admin + end-user billing APIs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.billing.service import BillingError, BillingService
from app.core.settings import get_settings
from app.db.models import BillingLedgerEntry, BillingPlan, BillingWallet, Role
from app.db.session import tenant_session
from app.tenancy.context import TenantContext

admin_router = APIRouter(prefix="/admin/billing", tags=["admin-billing"])
me_router = APIRouter(prefix="/api/me/billing", tags=["user-billing"])

AdminContext = Annotated[
    TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
]
MeContext = Annotated[
    TenantContext,
    Depends(require_roles(Role.platform_admin, Role.tenant_admin, Role.end_user)),
]
TenantSession = Annotated[AsyncSession, Depends(tenant_session)]


class PlanOut(BaseModel):
    id: uuid.UUID
    scope: Literal["platform", "tenant"]
    slug: str
    name: str
    description: str
    monthly_price_cents: int
    included_credits_monthly: int
    credits_per_1k_input_tokens: int
    credits_per_1k_output_tokens: int
    credit_pack_credits: int
    credit_pack_price_cents: int
    is_active: bool


class PlanUpsertIn(BaseModel):
    slug: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    monthly_price_cents: int = Field(ge=0, default=0)
    included_credits_monthly: int = Field(ge=0, default=0)
    credits_per_1k_input_tokens: int = Field(ge=1, default=10)
    credits_per_1k_output_tokens: int = Field(ge=1, default=30)
    credit_pack_credits: int = Field(ge=1, default=1000)
    credit_pack_price_cents: int = Field(ge=0, default=1000)
    is_active: bool = True


class WalletOut(BaseModel):
    id: uuid.UUID
    owner_type: Literal["tenant", "user"]
    owner_id: str
    balance_credits: int
    allowance_remaining: int
    available_credits: int
    plan_id: uuid.UUID | None
    subscription_status: str
    period_start: datetime | None
    period_end: datetime | None


class LedgerOut(BaseModel):
    id: uuid.UUID
    entry_type: str
    amount_credits: int
    balance_after: int
    description: str
    reference_type: str | None
    reference_id: str | None
    created_by: str
    created_at: datetime


class GrantIn(BaseModel):
    owner_type: Literal["tenant", "user"]
    owner_id: str
    credits: int = Field(ge=1, le=10_000_000)
    description: str = Field(default="Admin credit grant", max_length=500)


class SubscribeIn(BaseModel):
    plan_id: uuid.UUID
    owner_type: Literal["tenant", "user"] = "user"
    owner_id: str | None = None


class CheckoutIn(BaseModel):
    plan_id: uuid.UUID | None = None
    owner_type: Literal["tenant", "user"] = "user"
    owner_id: str | None = None
    success_url: str = Field(default="/admin/billing")
    cancel_url: str = Field(default="/admin/billing")


class CheckoutOut(BaseModel):
    checkout_id: str
    checkout_url: str | None
    status: Literal["completed", "pending"]
    provider: str
    wallet: WalletOut


def _plan_out(plan: BillingPlan) -> PlanOut:
    return PlanOut(
        id=plan.id,
        scope=plan.scope,  # type: ignore[arg-type]
        slug=plan.slug,
        name=plan.name,
        description=plan.description,
        monthly_price_cents=plan.monthly_price_cents,
        included_credits_monthly=plan.included_credits_monthly,
        credits_per_1k_input_tokens=plan.credits_per_1k_input_tokens,
        credits_per_1k_output_tokens=plan.credits_per_1k_output_tokens,
        credit_pack_credits=plan.credit_pack_credits,
        credit_pack_price_cents=plan.credit_pack_price_cents,
        is_active=plan.is_active,
    )


def _wallet_out(wallet: BillingWallet) -> WalletOut:
    available = max(0, wallet.balance_credits) + max(0, wallet.allowance_remaining)
    return WalletOut(
        id=wallet.id,
        owner_type=wallet.owner_type,  # type: ignore[arg-type]
        owner_id=wallet.owner_id,
        balance_credits=wallet.balance_credits,
        allowance_remaining=wallet.allowance_remaining,
        available_credits=available,
        plan_id=wallet.plan_id,
        subscription_status=wallet.subscription_status,
        period_start=wallet.period_start,
        period_end=wallet.period_end,
    )


def _ledger_out(entry: BillingLedgerEntry) -> LedgerOut:
    return LedgerOut(
        id=entry.id,
        entry_type=entry.entry_type,
        amount_credits=entry.amount_credits,
        balance_after=entry.balance_after,
        description=entry.description,
        reference_type=entry.reference_type,
        reference_id=entry.reference_id,
        created_by=entry.created_by,
        created_at=entry.created_at,
    )


def _public_url(path: str) -> str:
    base = get_settings().app_public_url.rstrip("/")
    if path.startswith("http"):
        return path
    return f"{base}/{path.lstrip('/')}"


@admin_router.get("/plans", response_model=list[PlanOut])
async def list_tenant_plans(
    context: AdminContext,
    session: TenantSession,
) -> list[PlanOut]:
    billing = BillingService(session, context)
    await billing.ensure_default_tenant_plan(context.tenant_id)
    plans = await billing.list_plans(scope="tenant", tenant_id=context.tenant_id)
    return [_plan_out(plan) for plan in plans]


@admin_router.put("/plans/{slug}", response_model=PlanOut)
async def upsert_tenant_plan(
    slug: str,
    body: PlanUpsertIn,
    context: AdminContext,
    session: TenantSession,
) -> PlanOut:
    billing = BillingService(session, context)
    plan = await billing.upsert_plan(
        {
            "scope": "tenant",
            "tenant_id": context.tenant_id,
            "slug": slug,
            **body.model_dump(),
        }
    )
    await session.commit()
    return _plan_out(plan)


@admin_router.get("/wallet", response_model=WalletOut)
async def get_tenant_wallet(
    context: AdminContext,
    session: TenantSession,
) -> WalletOut:
    billing = BillingService(session, context)
    wallet = await billing.provision_tenant_wallets(context.tenant_id)
    await session.commit()
    return _wallet_out(wallet)


@admin_router.get("/wallets/users/{user_id}", response_model=WalletOut)
async def get_user_wallet_admin(
    user_id: str,
    context: AdminContext,
    session: TenantSession,
) -> WalletOut:
    billing = BillingService(session, context)
    wallet = await billing.provision_user_wallet(context.tenant_id, user_id)
    await session.commit()
    return _wallet_out(wallet)


@admin_router.post("/grant", response_model=WalletOut)
async def grant_credits(
    body: GrantIn,
    context: AdminContext,
    session: TenantSession,
) -> WalletOut:
    billing = BillingService(session, context)
    owner_id = body.owner_id
    if body.owner_type == "tenant":
        owner_id = str(context.tenant_id)
    try:
        wallet = await billing.grant_credits(
            tenant_id=context.tenant_id,
            owner_type=body.owner_type,
            owner_id=owner_id,
            credits=body.credits,
            created_by=context.user_id,
            description=body.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return _wallet_out(wallet)


@admin_router.post("/subscribe", response_model=WalletOut)
async def subscribe(
    body: SubscribeIn,
    context: AdminContext,
    session: TenantSession,
) -> WalletOut:
    billing = BillingService(session, context)
    owner_type = body.owner_type
    owner_id = body.owner_id or (
        str(context.tenant_id) if owner_type == "tenant" else context.user_id
    )
    try:
        wallet = await billing.subscribe_wallet(
            tenant_id=context.tenant_id,
            owner_type=owner_type,
            owner_id=owner_id,
            plan_id=body.plan_id,
            created_by=context.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return _wallet_out(wallet)


@admin_router.post("/checkout/subscription", response_model=CheckoutOut)
async def checkout_subscription(
    body: CheckoutIn,
    context: AdminContext,
    session: TenantSession,
) -> CheckoutOut:
    if body.plan_id is None:
        raise HTTPException(status_code=400, detail="plan_id is required")
    billing = BillingService(session, context)
    owner_type = body.owner_type
    owner_id = body.owner_id or (
        str(context.tenant_id) if owner_type == "tenant" else context.user_id
    )
    try:
        wallet, checkout = await billing.start_subscription_checkout(
            tenant_id=context.tenant_id,
            owner_type=owner_type,
            owner_id=owner_id,
            plan_id=body.plan_id,
            created_by=context.user_id,
            success_url=_public_url(body.success_url),
            cancel_url=_public_url(body.cancel_url),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return CheckoutOut(
        checkout_id=checkout.checkout_id,
        checkout_url=checkout.checkout_url,
        status=checkout.status,
        provider=checkout.provider,
        wallet=_wallet_out(wallet),
    )


@admin_router.post("/checkout/credit-pack", response_model=CheckoutOut)
async def checkout_credit_pack(
    body: CheckoutIn,
    context: AdminContext,
    session: TenantSession,
) -> CheckoutOut:
    billing = BillingService(session, context)
    owner_type = body.owner_type
    owner_id = body.owner_id or (
        str(context.tenant_id) if owner_type == "tenant" else context.user_id
    )
    try:
        wallet, checkout = await billing.purchase_credit_pack(
            tenant_id=context.tenant_id,
            owner_type=owner_type,
            owner_id=owner_id,
            plan_id=body.plan_id,
            created_by=context.user_id,
            success_url=_public_url(body.success_url),
            cancel_url=_public_url(body.cancel_url),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return CheckoutOut(
        checkout_id=checkout.checkout_id,
        checkout_url=checkout.checkout_url,
        status=checkout.status,
        provider=checkout.provider,
        wallet=_wallet_out(wallet),
    )


@admin_router.get("/ledger", response_model=list[LedgerOut])
async def tenant_ledger(
    context: AdminContext,
    session: TenantSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[LedgerOut]:
    billing = BillingService(session, context)
    wallet = await billing.provision_tenant_wallets(context.tenant_id)
    entries = await billing.list_ledger(
        tenant_id=context.tenant_id, wallet_id=wallet.id, limit=limit
    )
    return [_ledger_out(entry) for entry in entries]


@me_router.get("/wallet", response_model=WalletOut)
async def my_wallet(
    context: MeContext,
    session: TenantSession,
) -> WalletOut:
    billing = BillingService(session, context)
    wallet = await billing.provision_user_wallet(context.tenant_id, context.user_id)
    await session.commit()
    return _wallet_out(wallet)


@me_router.get("/plans", response_model=list[PlanOut])
async def my_plans(
    context: MeContext,
    session: TenantSession,
) -> list[PlanOut]:
    billing = BillingService(session, context)
    await billing.ensure_default_tenant_plan(context.tenant_id)
    plans = await billing.list_plans(scope="tenant", tenant_id=context.tenant_id)
    return [_plan_out(plan) for plan in plans if plan.is_active]


@me_router.post("/checkout/subscription", response_model=CheckoutOut)
async def my_checkout_subscription(
    body: CheckoutIn,
    context: MeContext,
    session: TenantSession,
) -> CheckoutOut:
    if body.plan_id is None:
        raise HTTPException(status_code=400, detail="plan_id is required")
    billing = BillingService(session, context)
    try:
        wallet, checkout = await billing.start_subscription_checkout(
            tenant_id=context.tenant_id,
            owner_type="user",
            owner_id=context.user_id,
            plan_id=body.plan_id,
            created_by=context.user_id,
            success_url=_public_url(body.success_url or "/chat"),
            cancel_url=_public_url(body.cancel_url or "/chat"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return CheckoutOut(
        checkout_id=checkout.checkout_id,
        checkout_url=checkout.checkout_url,
        status=checkout.status,
        provider=checkout.provider,
        wallet=_wallet_out(wallet),
    )


@me_router.post("/checkout/credit-pack", response_model=CheckoutOut)
async def my_checkout_credit_pack(
    body: CheckoutIn,
    context: MeContext,
    session: TenantSession,
) -> CheckoutOut:
    billing = BillingService(session, context)
    try:
        wallet, checkout = await billing.purchase_credit_pack(
            tenant_id=context.tenant_id,
            owner_type="user",
            owner_id=context.user_id,
            plan_id=body.plan_id,
            created_by=context.user_id,
            success_url=_public_url(body.success_url or "/chat"),
            cancel_url=_public_url(body.cancel_url or "/chat"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return CheckoutOut(
        checkout_id=checkout.checkout_id,
        checkout_url=checkout.checkout_url,
        status=checkout.status,
        provider=checkout.provider,
        wallet=_wallet_out(wallet),
    )


@me_router.get("/ledger", response_model=list[LedgerOut])
async def my_ledger(
    context: MeContext,
    session: TenantSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[LedgerOut]:
    billing = BillingService(session, context)
    wallet = await billing.provision_user_wallet(context.tenant_id, context.user_id)
    entries = await billing.list_ledger(
        tenant_id=context.tenant_id, wallet_id=wallet.id, limit=limit
    )
    return [_ledger_out(entry) for entry in entries]
