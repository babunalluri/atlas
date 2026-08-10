from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.metering import credits_for_tokens, extract_token_metrics
from app.billing.provider import CheckoutResult, get_billing_provider
from app.core.settings import Settings, get_settings
from app.db.models import BillingLedgerEntry, BillingPlan, BillingWallet
from app.db.repositories import new_id
from app.tenancy.context import TenantContext


class BillingError(Exception):
    """Raised when a billing rule blocks an action."""


OwnerType = Literal["tenant", "user"]


def wallet_available(wallet: BillingWallet) -> int:
    return max(0, wallet.balance_credits) + max(0, wallet.allowance_remaining)


class BillingService:
    DEFAULT_PLATFORM_PLAN = {
        "slug": "platform-starter",
        "name": "Platform Starter",
        "description": "Default platform plan for new organizations.",
        "monthly_price_cents": 0,
        "included_credits_monthly": 50_000,
        "credits_per_1k_input_tokens": 8,
        "credits_per_1k_output_tokens": 24,
        "credit_pack_credits": 10_000,
        "credit_pack_price_cents": 1000,
    }
    DEFAULT_TENANT_PLAN = {
        "slug": "team-starter",
        "name": "Team Starter",
        "description": "Default end-user plan for this organization.",
        "monthly_price_cents": 0,
        "included_credits_monthly": 5_000,
        "credits_per_1k_input_tokens": 10,
        "credits_per_1k_output_tokens": 30,
        "credit_pack_credits": 1_000,
        "credit_pack_price_cents": 500,
    }

    def __init__(
        self,
        session: AsyncSession,
        context: TenantContext | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.context = context
        self.settings = settings or get_settings()

    async def ensure_default_platform_plan(self) -> BillingPlan:
        row = await self.session.scalar(
            select(BillingPlan).where(
                BillingPlan.scope == "platform",
                BillingPlan.slug == self.DEFAULT_PLATFORM_PLAN["slug"],
            )
        )
        if row is not None:
            return row
        plan = BillingPlan(
            id=new_id(),
            scope="platform",
            tenant_id=None,
            **self.DEFAULT_PLATFORM_PLAN,
            is_active=True,
        )
        self.session.add(plan)
        await self.session.flush()
        return plan

    async def ensure_default_tenant_plan(self, tenant_id: uuid.UUID) -> BillingPlan:
        row = await self.session.scalar(
            select(BillingPlan).where(
                BillingPlan.scope == "tenant",
                BillingPlan.tenant_id == tenant_id,
                BillingPlan.slug == self.DEFAULT_TENANT_PLAN["slug"],
            )
        )
        if row is not None:
            return row
        plan = BillingPlan(
            id=new_id(),
            scope="tenant",
            tenant_id=tenant_id,
            **self.DEFAULT_TENANT_PLAN,
            is_active=True,
        )
        self.session.add(plan)
        await self.session.flush()
        return plan

    async def get_wallet(
        self,
        *,
        tenant_id: uuid.UUID,
        owner_type: OwnerType,
        owner_id: str,
    ) -> BillingWallet | None:
        return await self.session.scalar(
            select(BillingWallet).where(
                BillingWallet.tenant_id == tenant_id,
                BillingWallet.owner_type == owner_type,
                BillingWallet.owner_id == owner_id,
            )
        )

    async def get_or_create_wallet(
        self,
        *,
        tenant_id: uuid.UUID,
        owner_type: OwnerType,
        owner_id: str,
        plan: BillingPlan | None = None,
        starter_credits: int = 0,
    ) -> BillingWallet:
        wallet = await self.get_wallet(
            tenant_id=tenant_id, owner_type=owner_type, owner_id=owner_id
        )
        if wallet is not None:
            return wallet
        wallet = BillingWallet(
            id=new_id(),
            tenant_id=tenant_id,
            owner_type=owner_type,
            owner_id=owner_id,
            balance_credits=starter_credits,
            allowance_remaining=plan.included_credits_monthly if plan else 0,
            plan_id=plan.id if plan else None,
            subscription_status="active" if plan else "none",
            period_start=datetime.now(UTC) if plan else None,
            period_end=(datetime.now(UTC) + timedelta(days=30)) if plan else None,
        )
        self.session.add(wallet)
        await self.session.flush()
        if starter_credits:
            await self._append_ledger(
                wallet,
                entry_type="grant",
                amount_credits=starter_credits,
                reference_type="bootstrap",
                reference_id=str(wallet.id),
                description="Starter credits",
                created_by="system",
            )
        return wallet

    async def provision_tenant_wallets(self, tenant_id: uuid.UUID) -> BillingWallet:
        platform_plan = await self.ensure_default_platform_plan()
        return await self.get_or_create_wallet(
            tenant_id=tenant_id,
            owner_type="tenant",
            owner_id=str(tenant_id),
            plan=platform_plan,
        )

    async def provision_user_wallet(
        self, tenant_id: uuid.UUID, user_id: str
    ) -> BillingWallet:
        tenant_plan = await self.ensure_default_tenant_plan(tenant_id)
        return await self.get_or_create_wallet(
            tenant_id=tenant_id,
            owner_type="user",
            owner_id=user_id,
            plan=tenant_plan,
        )

    async def assert_can_run(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: str,
        admin_preview: bool = False,
        scheduler: bool = False,
    ) -> None:
        tenant_wallet = await self.get_wallet(
            tenant_id=tenant_id, owner_type="tenant", owner_id=str(tenant_id)
        )
        if tenant_wallet is None:
            tenant_wallet = await self.provision_tenant_wallets(tenant_id)
        if wallet_available(tenant_wallet) <= 0:
            raise BillingError("Organization has no credits remaining")

        if scheduler or admin_preview:
            return

        user_wallet = await self.get_wallet(
            tenant_id=tenant_id, owner_type="user", owner_id=user_id
        )
        if user_wallet is None:
            user_wallet = await self.provision_user_wallet(tenant_id, user_id)
        if wallet_available(user_wallet) <= 0:
            raise BillingError("You have no credits remaining")

    async def record_run_usage(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: str,
        run_id: str | None,
        payload: dict[str, Any],
        admin_preview: bool = False,
        scheduler: bool = False,
    ) -> None:
        input_tokens, output_tokens = extract_token_metrics(payload)
        tenant_wallet = await self.get_wallet(
            tenant_id=tenant_id, owner_type="tenant", owner_id=str(tenant_id)
        )
        if tenant_wallet is None:
            tenant_wallet = await self.provision_tenant_wallets(tenant_id)
        tenant_plan = await self._plan_for_wallet(tenant_wallet)
        tenant_credits = credits_for_tokens(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            credits_per_1k_input=tenant_plan.credits_per_1k_input_tokens,
            credits_per_1k_output=tenant_plan.credits_per_1k_output_tokens,
        )
        await self._debit_wallet(
            tenant_wallet,
            tenant_credits,
            entry_type="usage",
            reference_type="run",
            reference_id=run_id,
            description="Platform usage",
            created_by=user_id,
            details={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "credits": tenant_credits,
            },
        )

        if scheduler or admin_preview:
            return

        user_wallet = await self.get_wallet(
            tenant_id=tenant_id, owner_type="user", owner_id=user_id
        )
        if user_wallet is None:
            user_wallet = await self.provision_user_wallet(tenant_id, user_id)
        user_plan = await self._plan_for_wallet(user_wallet)
        user_credits = credits_for_tokens(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            credits_per_1k_input=user_plan.credits_per_1k_input_tokens,
            credits_per_1k_output=user_plan.credits_per_1k_output_tokens,
        )
        await self._debit_wallet(
            user_wallet,
            user_credits,
            entry_type="usage",
            reference_type="run",
            reference_id=run_id,
            description="Usage charge",
            created_by=user_id,
            details={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "credits": user_credits,
            },
        )

    async def list_plans(
        self, *, scope: Literal["platform", "tenant"], tenant_id: uuid.UUID | None = None
    ) -> list[BillingPlan]:
        stmt = select(BillingPlan).where(BillingPlan.scope == scope)
        if scope == "tenant":
            if tenant_id is None:
                raise ValueError("tenant_id is required for tenant-scoped plans")
            stmt = stmt.where(BillingPlan.tenant_id == tenant_id)
        else:
            stmt = stmt.where(BillingPlan.tenant_id.is_(None))
        return list((await self.session.scalars(stmt.order_by(BillingPlan.name))).all())

    async def upsert_plan(self, values: dict[str, Any]) -> BillingPlan:
        scope = values["scope"]
        tenant_id = values.get("tenant_id")
        slug = values["slug"]
        existing = await self.session.scalar(
            select(BillingPlan).where(
                BillingPlan.scope == scope,
                BillingPlan.tenant_id == tenant_id,
                BillingPlan.slug == slug,
            )
        )
        if existing is None:
            plan = BillingPlan(id=new_id(), **values)
            self.session.add(plan)
            await self.session.flush()
            return plan
        for key, value in values.items():
            if key in {"id", "scope", "tenant_id", "slug"}:
                continue
            setattr(existing, key, value)
        await self.session.flush()
        return existing

    async def grant_credits(
        self,
        *,
        tenant_id: uuid.UUID,
        owner_type: OwnerType,
        owner_id: str,
        credits: int,
        created_by: str,
        description: str,
    ) -> BillingWallet:
        if credits <= 0:
            raise ValueError("Grant amount must be positive")
        wallet = await self.get_or_create_wallet(
            tenant_id=tenant_id,
            owner_type=owner_type,
            owner_id=owner_id,
        )
        wallet.balance_credits += credits
        await self._append_ledger(
            wallet,
            entry_type="grant",
            amount_credits=credits,
            reference_type="admin",
            reference_id=created_by,
            description=description,
            created_by=created_by,
        )
        await self.session.flush()
        return wallet

    async def subscribe_wallet(
        self,
        *,
        tenant_id: uuid.UUID,
        owner_type: OwnerType,
        owner_id: str,
        plan_id: uuid.UUID,
        created_by: str,
    ) -> BillingWallet:
        plan = await self.session.get(BillingPlan, plan_id)
        if plan is None or not plan.is_active:
            raise ValueError("Plan not found")
        if owner_type == "tenant" and plan.scope != "platform":
            raise ValueError("Tenant wallets require a platform plan")
        if owner_type == "user" and (
            plan.scope != "tenant" or plan.tenant_id != tenant_id
        ):
            raise ValueError("User wallets require a tenant plan")
        wallet = await self.get_or_create_wallet(
            tenant_id=tenant_id,
            owner_type=owner_type,
            owner_id=owner_id,
        )
        now = datetime.now(UTC)
        wallet.plan_id = plan.id
        wallet.subscription_status = "active"
        wallet.period_start = now
        wallet.period_end = now + timedelta(days=30)
        wallet.allowance_remaining = plan.included_credits_monthly
        await self._append_ledger(
            wallet,
            entry_type="subscription_grant",
            amount_credits=plan.included_credits_monthly,
            reference_type="subscription",
            reference_id=str(plan.id),
            description=f"Monthly allowance — {plan.name}",
            created_by=created_by,
        )
        await self.session.flush()
        return wallet

    async def purchase_credit_pack(
        self,
        *,
        tenant_id: uuid.UUID,
        owner_type: OwnerType,
        owner_id: str,
        plan_id: uuid.UUID | None,
        created_by: str,
        success_url: str,
        cancel_url: str,
    ) -> tuple[BillingWallet, CheckoutResult]:
        wallet = await self.get_or_create_wallet(
            tenant_id=tenant_id,
            owner_type=owner_type,
            owner_id=owner_id,
        )
        plan = await self._resolve_pack_plan(wallet, plan_id, tenant_id, owner_type)
        provider = get_billing_provider(self.settings)
        checkout = await provider.create_credit_pack_checkout(
            wallet_id=wallet.id,
            tenant_id=tenant_id,
            pack_credits=plan.credit_pack_credits,
            pack_price_cents=plan.credit_pack_price_cents,
            success_url=success_url,
            cancel_url=cancel_url,
            customer_id=self._provider_customer_id(wallet),
        )
        if checkout.status == "completed":
            wallet.balance_credits += plan.credit_pack_credits
            await self._append_ledger(
                wallet,
                entry_type="purchase",
                amount_credits=plan.credit_pack_credits,
                reference_type="checkout",
                reference_id=checkout.checkout_id,
                description=f"Credit pack — {plan.credit_pack_credits:,} credits",
                created_by=created_by,
                details={"provider": checkout.provider, "price_cents": plan.credit_pack_price_cents},
            )
            await self.session.flush()
        return wallet, checkout

    async def start_subscription_checkout(
        self,
        *,
        tenant_id: uuid.UUID,
        owner_type: OwnerType,
        owner_id: str,
        plan_id: uuid.UUID,
        created_by: str,
        success_url: str,
        cancel_url: str,
    ) -> tuple[BillingWallet, CheckoutResult]:
        plan = await self.session.get(BillingPlan, plan_id)
        if plan is None or not plan.is_active:
            raise ValueError("Plan not found")
        wallet = await self.get_or_create_wallet(
            tenant_id=tenant_id,
            owner_type=owner_type,
            owner_id=owner_id,
        )
        provider = get_billing_provider(self.settings)
        checkout = await provider.create_subscription_checkout(
            wallet_id=wallet.id,
            tenant_id=tenant_id,
            plan_name=plan.name,
            monthly_price_cents=plan.monthly_price_cents,
            provider_plan_id=self._provider_plan_id(plan),
            billing_plan_id=plan.id,
            success_url=success_url,
            cancel_url=cancel_url,
            customer_id=self._provider_customer_id(wallet),
        )
        if checkout.status == "completed":
            await self.subscribe_wallet(
                tenant_id=tenant_id,
                owner_type=owner_type,
                owner_id=owner_id,
                plan_id=plan.id,
                created_by=created_by,
            )
            wallet = await self.get_wallet(
                tenant_id=tenant_id, owner_type=owner_type, owner_id=owner_id
            )
            if wallet is None:
                raise RuntimeError("Wallet missing after subscription")
        return wallet, checkout

    async def fulfill_provider_checkout(
        self,
        *,
        tenant_id: uuid.UUID,
        wallet_id: uuid.UUID,
        kind: str,
        checkout_id: str,
        pack_credits: int | None = None,
        plan_id: uuid.UUID | None = None,
        created_by: str = "razorpay_webhook",
    ) -> BillingWallet | None:
        wallet = await self.session.get(BillingWallet, wallet_id)
        if wallet is None or wallet.tenant_id != tenant_id:
            return None
        existing = await self.session.scalar(
            select(BillingLedgerEntry).where(
                BillingLedgerEntry.tenant_id == tenant_id,
                BillingLedgerEntry.reference_type == "checkout",
                BillingLedgerEntry.reference_id == checkout_id,
            )
        )
        if existing is not None:
            return wallet

        if kind == "credit_pack" and pack_credits and pack_credits > 0:
            wallet.balance_credits += pack_credits
            await self._append_ledger(
                wallet,
                entry_type="purchase",
                amount_credits=pack_credits,
                reference_type="checkout",
                reference_id=checkout_id,
                description=f"Credit pack — {pack_credits:,} credits",
                created_by=created_by,
                details={"provider": self.settings.billing_provider},
            )
            await self.session.flush()
            return wallet

        if kind in {"subscription", "subscription_payment"} and plan_id is not None:
            return await self.subscribe_wallet(
                tenant_id=tenant_id,
                owner_type=wallet.owner_type,  # type: ignore[arg-type]
                owner_id=wallet.owner_id,
                plan_id=plan_id,
                created_by=created_by,
            )

        if kind in {"subscription", "subscription_payment"} and wallet.plan_id:
            plan = await self.session.get(BillingPlan, wallet.plan_id)
            if plan is not None:
                return await self.subscribe_wallet(
                    tenant_id=tenant_id,
                    owner_type=wallet.owner_type,  # type: ignore[arg-type]
                    owner_id=wallet.owner_id,
                    plan_id=plan.id,
                    created_by=created_by,
                )
        return wallet

    def _provider_plan_id(self, plan: BillingPlan) -> str | None:
        if self.settings.billing_provider.strip().lower() == "razorpay":
            return plan.razorpay_monthly_plan_id
        return plan.stripe_monthly_price_id

    def _provider_customer_id(self, wallet: BillingWallet) -> str | None:
        if self.settings.billing_provider.strip().lower() == "razorpay":
            return wallet.razorpay_customer_id
        return wallet.stripe_customer_id

    async def list_ledger(
        self,
        *,
        tenant_id: uuid.UUID,
        wallet_id: uuid.UUID,
        limit: int = 50,
    ) -> list[BillingLedgerEntry]:
        return list(
            (
                await self.session.scalars(
                    select(BillingLedgerEntry)
                    .where(
                        BillingLedgerEntry.tenant_id == tenant_id,
                        BillingLedgerEntry.wallet_id == wallet_id,
                    )
                    .order_by(BillingLedgerEntry.created_at.desc())
                    .limit(limit)
                )
            ).all()
        )

    async def _plan_for_wallet(self, wallet: BillingWallet) -> BillingPlan:
        if wallet.plan_id:
            plan = await self.session.get(BillingPlan, wallet.plan_id)
            if plan is not None and plan.is_active:
                return plan
        if wallet.owner_type == "tenant":
            return await self.ensure_default_platform_plan()
        return await self.ensure_default_tenant_plan(wallet.tenant_id)

    async def _resolve_pack_plan(
        self,
        wallet: BillingWallet,
        plan_id: uuid.UUID | None,
        tenant_id: uuid.UUID,
        owner_type: OwnerType,
    ) -> BillingPlan:
        if plan_id is not None:
            plan = await self.session.get(BillingPlan, plan_id)
            if plan is None or not plan.is_active:
                raise ValueError("Plan not found")
            return plan
        return await self._plan_for_wallet(wallet)

    async def _debit_wallet(
        self,
        wallet: BillingWallet,
        credits: int,
        *,
        entry_type: str,
        reference_type: str | None,
        reference_id: str | None,
        description: str,
        created_by: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        remaining = credits
        if wallet.allowance_remaining > 0 and remaining > 0:
            from_allowance = min(wallet.allowance_remaining, remaining)
            wallet.allowance_remaining -= from_allowance
            remaining -= from_allowance
        if remaining > 0:
            wallet.balance_credits = max(0, wallet.balance_credits - remaining)
        await self._append_ledger(
            wallet,
            entry_type=entry_type,
            amount_credits=-credits,
            reference_type=reference_type,
            reference_id=reference_id,
            description=description,
            created_by=created_by,
            details=details or {},
        )

    async def _append_ledger(
        self,
        wallet: BillingWallet,
        *,
        entry_type: str,
        amount_credits: int,
        reference_type: str | None,
        reference_id: str | None,
        description: str,
        created_by: str,
        details: dict[str, Any] | None = None,
    ) -> BillingLedgerEntry:
        entry = BillingLedgerEntry(
            id=new_id(),
            tenant_id=wallet.tenant_id,
            wallet_id=wallet.id,
            entry_type=entry_type,
            amount_credits=amount_credits,
            balance_after=wallet_available(wallet),
            reference_type=reference_type,
            reference_id=reference_id,
            description=description,
            details=details or {},
            created_by=created_by,
        )
        self.session.add(entry)
        return entry
