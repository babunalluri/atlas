from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal, Protocol

from app.billing.razorpay_client import RazorpayClient, RazorpayError
from app.core.settings import Settings


@dataclass(frozen=True)
class CheckoutResult:
    checkout_id: str
    checkout_url: str | None
    status: Literal["completed", "pending"]
    provider: str


class BillingProvider(Protocol):
    async def create_subscription_checkout(
        self,
        *,
        wallet_id: uuid.UUID,
        tenant_id: uuid.UUID,
        plan_name: str,
        monthly_price_cents: int,
        provider_plan_id: str | None,
        billing_plan_id: uuid.UUID | None,
        success_url: str,
        cancel_url: str,
        customer_id: str | None,
    ) -> CheckoutResult: ...

    async def create_credit_pack_checkout(
        self,
        *,
        wallet_id: uuid.UUID,
        tenant_id: uuid.UUID,
        pack_credits: int,
        pack_price_cents: int,
        success_url: str,
        cancel_url: str,
        customer_id: str | None,
    ) -> CheckoutResult: ...


class DummyBillingProvider:
    """Instantly completes checkouts for local/dev billing."""

    provider_name = "dummy"

    async def create_subscription_checkout(
        self,
        *,
        wallet_id: uuid.UUID,
        tenant_id: uuid.UUID,
        plan_name: str,
        monthly_price_cents: int,
        provider_plan_id: str | None,
        billing_plan_id: uuid.UUID | None,
        success_url: str,
        cancel_url: str,
        customer_id: str | None,
    ) -> CheckoutResult:
        del tenant_id, plan_name, monthly_price_cents, provider_plan_id, billing_plan_id, cancel_url, customer_id
        return CheckoutResult(
            checkout_id=f"dummy_sub_{wallet_id}",
            checkout_url=success_url,
            status="completed",
            provider=self.provider_name,
        )

    async def create_credit_pack_checkout(
        self,
        *,
        wallet_id: uuid.UUID,
        tenant_id: uuid.UUID,
        pack_credits: int,
        pack_price_cents: int,
        success_url: str,
        cancel_url: str,
        customer_id: str | None,
    ) -> CheckoutResult:
        del tenant_id, pack_price_cents, cancel_url, customer_id
        return CheckoutResult(
            checkout_id=f"dummy_pack_{wallet_id}_{pack_credits}",
            checkout_url=success_url,
            status="completed",
            provider=self.provider_name,
        )


class RazorpayProvider:
    """Live Razorpay checkout via payment links and optional subscription plans."""

    provider_name = "razorpay"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = RazorpayClient(settings)

    async def create_subscription_checkout(
        self,
        *,
        wallet_id: uuid.UUID,
        tenant_id: uuid.UUID,
        plan_name: str,
        monthly_price_cents: int,
        provider_plan_id: str | None,
        billing_plan_id: uuid.UUID | None,
        success_url: str,
        cancel_url: str,
        customer_id: str | None,
    ) -> CheckoutResult:
        del cancel_url
        notes = {
            "tenant_id": str(tenant_id),
            "wallet_id": str(wallet_id),
            "kind": "subscription",
        }
        if billing_plan_id is not None:
            notes["plan_id"] = str(billing_plan_id)
        if provider_plan_id:
            try:
                sub = await self._client.create_subscription(
                    plan_id=provider_plan_id,
                    total_count=120,
                    customer_id=customer_id,
                    notes=notes,
                )
            except RazorpayError as exc:
                raise ValueError(str(exc)) from exc
            checkout_url = sub.get("short_url") or success_url
            return CheckoutResult(
                checkout_id=str(sub.get("id") or f"sub_{wallet_id}"),
                checkout_url=str(checkout_url) if checkout_url else None,
                status="pending",
                provider=self.provider_name,
            )
        if monthly_price_cents <= 0:
            return CheckoutResult(
                checkout_id=f"razorpay_free_sub_{wallet_id}",
                checkout_url=success_url,
                status="completed",
                provider=self.provider_name,
            )
        return await self._payment_link(
            wallet_id=wallet_id,
            tenant_id=tenant_id,
            amount_paise=monthly_price_cents,
            description=f"Subscribe — {plan_name}",
            success_url=success_url,
            notes={**notes, "kind": "subscription_payment"},
            reference_prefix="sub",
        )

    async def create_credit_pack_checkout(
        self,
        *,
        wallet_id: uuid.UUID,
        tenant_id: uuid.UUID,
        pack_credits: int,
        pack_price_cents: int,
        success_url: str,
        cancel_url: str,
        customer_id: str | None,
    ) -> CheckoutResult:
        del cancel_url, customer_id
        if pack_price_cents <= 0:
            return CheckoutResult(
                checkout_id=f"razorpay_free_pack_{wallet_id}",
                checkout_url=success_url,
                status="completed",
                provider=self.provider_name,
            )
        return await self._payment_link(
            wallet_id=wallet_id,
            tenant_id=tenant_id,
            amount_paise=pack_price_cents,
            description=f"Credit pack — {pack_credits:,} credits",
            success_url=success_url,
            notes={
                "tenant_id": str(tenant_id),
                "wallet_id": str(wallet_id),
                "kind": "credit_pack",
                "pack_credits": str(pack_credits),
            },
            reference_prefix="pack",
        )

    async def _payment_link(
        self,
        *,
        wallet_id: uuid.UUID,
        tenant_id: uuid.UUID,
        amount_paise: int,
        description: str,
        success_url: str,
        notes: dict[str, str],
        reference_prefix: str,
    ) -> CheckoutResult:
        try:
            link = await self._client.create_payment_link(
                amount_paise=amount_paise,
                description=description,
                customer_name=f"wallet-{wallet_id}",
                callback_url=success_url,
                notes=notes,
                reference_id=f"{reference_prefix}_{wallet_id}",
            )
        except RazorpayError as exc:
            raise ValueError(str(exc)) from exc
        checkout_url = link.get("short_url") or link.get("url") or success_url
        return CheckoutResult(
            checkout_id=str(link.get("id") or f"{reference_prefix}_{wallet_id}"),
            checkout_url=str(checkout_url) if checkout_url else None,
            status="pending",
            provider=self.provider_name,
        )


class StripeProvider:
    """Optional Stripe integration for non-INR deployments."""

    provider_name = "stripe"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def create_subscription_checkout(
        self,
        *,
        wallet_id: uuid.UUID,
        tenant_id: uuid.UUID,
        plan_name: str,
        monthly_price_cents: int,
        provider_plan_id: str | None,
        billing_plan_id: uuid.UUID | None,
        success_url: str,
        cancel_url: str,
        customer_id: str | None,
    ) -> CheckoutResult:
        del wallet_id, tenant_id, plan_name, monthly_price_cents, success_url, cancel_url, customer_id
        if not provider_plan_id:
            raise ValueError("Stripe monthly price id is required for live checkout")
        raise NotImplementedError("Live Stripe checkout is not wired yet; use razorpay or dummy")

    async def create_credit_pack_checkout(
        self,
        *,
        wallet_id: uuid.UUID,
        tenant_id: uuid.UUID,
        pack_credits: int,
        pack_price_cents: int,
        success_url: str,
        cancel_url: str,
        customer_id: str | None,
    ) -> CheckoutResult:
        del wallet_id, tenant_id, pack_credits, pack_price_cents, success_url, cancel_url, customer_id
        raise NotImplementedError("Live Stripe checkout is not wired yet; use razorpay or dummy")


def get_billing_provider(settings: Settings) -> BillingProvider:
    provider = settings.billing_provider.strip().lower()
    if provider == "razorpay":
        key = settings.razorpay_key_id.strip()
        secret = settings.razorpay_key_secret.get_secret_value().strip()
        if key and secret:
            return RazorpayProvider(settings)
    if provider == "stripe":
        if settings.stripe_secret_key.get_secret_value().strip():
            return StripeProvider(settings)
    return DummyBillingProvider()
