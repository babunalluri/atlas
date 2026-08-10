"""Prepaid billing: wallets, debit, hard-stop."""

import pytest
from fastapi import HTTPException

from app.billing.enforcement import require_credits_for_run
from app.billing.service import BillingError, BillingService, wallet_available
from app.db.models import Role
from app.tenancy.context import TenantContext


@pytest.mark.asyncio
async def test_provision_tenant_wallet_with_allowance(session, tenant_a):
    billing = BillingService(session, tenant_a)
    wallet = await billing.provision_tenant_wallets(tenant_a.tenant_id)
    assert wallet.owner_type == "tenant"
    assert wallet_available(wallet) > 0
    assert wallet.allowance_remaining > 0


@pytest.mark.asyncio
async def test_grant_and_debit_user_wallet(session, tenant_a):
    billing = BillingService(session, tenant_a)
    user_ctx = TenantContext(
        tenant_id=tenant_a.tenant_id,
        user_id="user_billing",
        role=Role.end_user,
        clerk_org_id=tenant_a.clerk_org_id,
    )
    await billing.provision_tenant_wallets(tenant_a.tenant_id)
    wallet = await billing.provision_user_wallet(tenant_a.tenant_id, user_ctx.user_id)
    before = wallet_available(wallet)
    await billing.record_run_usage(
        tenant_id=tenant_a.tenant_id,
        user_id=user_ctx.user_id,
        run_id="run-1",
        payload={
            "metrics": {"input_tokens": 500, "output_tokens": 500},
        },
    )
    wallet = await billing.get_wallet(
        tenant_id=tenant_a.tenant_id,
        owner_type="user",
        owner_id=user_ctx.user_id,
    )
    assert wallet is not None
    assert wallet_available(wallet) < before


@pytest.mark.asyncio
async def test_hard_stop_when_no_credits(session, tenant_a):
    billing = BillingService(session, tenant_a)
    user_ctx = TenantContext(
        tenant_id=tenant_a.tenant_id,
        user_id="user_empty",
        role=Role.end_user,
        clerk_org_id=tenant_a.clerk_org_id,
    )
    tenant_wallet = await billing.provision_tenant_wallets(tenant_a.tenant_id)
    tenant_wallet.balance_credits = 0
    tenant_wallet.allowance_remaining = 0
    user_wallet = await billing.provision_user_wallet(tenant_a.tenant_id, user_ctx.user_id)
    user_wallet.balance_credits = 0
    user_wallet.allowance_remaining = 0
    await session.flush()

    with pytest.raises(BillingError):
        await billing.assert_can_run(
            tenant_id=tenant_a.tenant_id,
            user_id=user_ctx.user_id,
        )


@pytest.mark.asyncio
async def test_require_credits_raises_http_402(session, tenant_a):
    billing = BillingService(session, tenant_a)
    user_ctx = TenantContext(
        tenant_id=tenant_a.tenant_id,
        user_id="user_http",
        role=Role.end_user,
        clerk_org_id=tenant_a.clerk_org_id,
    )
    tenant_wallet = await billing.provision_tenant_wallets(tenant_a.tenant_id)
    tenant_wallet.balance_credits = 0
    tenant_wallet.allowance_remaining = 0
    user_wallet = await billing.provision_user_wallet(tenant_a.tenant_id, user_ctx.user_id)
    user_wallet.balance_credits = 0
    user_wallet.allowance_remaining = 0
    await session.flush()

    with pytest.raises(HTTPException) as exc:
        await require_credits_for_run(session, user_ctx)
    assert exc.value.status_code == 402


@pytest.mark.asyncio
async def test_dummy_credit_pack_purchase(session, tenant_a):
    billing = BillingService(session, tenant_a)
    await billing.provision_tenant_wallets(tenant_a.tenant_id)
    wallet = await billing.provision_user_wallet(tenant_a.tenant_id, "buyer")
    before = wallet.balance_credits
    updated, checkout = await billing.purchase_credit_pack(
        tenant_id=tenant_a.tenant_id,
        owner_type="user",
        owner_id="buyer",
        plan_id=None,
        created_by=tenant_a.user_id,
        success_url="http://localhost/billing",
        cancel_url="http://localhost/billing",
    )
    assert checkout.status == "completed"
    assert updated.balance_credits > before


@pytest.mark.asyncio
async def test_org_admin_grants_user_credits(session, tenant_a):
    billing = BillingService(session, tenant_a)
    await billing.provision_tenant_wallets(tenant_a.tenant_id)
    wallet = await billing.grant_credits(
        tenant_id=tenant_a.tenant_id,
        owner_type="user",
        owner_id="user_grantee",
        credits=2500,
        created_by=tenant_a.user_id,
        description="Org admin credit grant",
    )
    assert wallet.balance_credits >= 2500
    assert wallet.owner_type == "user"
    assert wallet.owner_id == "user_grantee"


@pytest.mark.asyncio
async def test_platform_grants_tenant_org_credits(session, tenant_a):
    billing = BillingService(session)
    wallet = await billing.grant_credits(
        tenant_id=tenant_a.tenant_id,
        owner_type="tenant",
        owner_id=str(tenant_a.tenant_id),
        credits=10_000,
        created_by="platform-owner",
        description="Platform credit grant",
    )
    assert wallet.balance_credits >= 10_000
    assert wallet.owner_type == "tenant"


@pytest.mark.asyncio
async def test_scheduler_only_debits_tenant_wallet(session, tenant_a):
    billing = BillingService(session, tenant_a)
    await billing.provision_tenant_wallets(tenant_a.tenant_id)
    user_wallet = await billing.provision_user_wallet(tenant_a.tenant_id, "atlas-scheduler")
    user_before = wallet_available(user_wallet)
    tenant_wallet = await billing.get_wallet(
        tenant_id=tenant_a.tenant_id,
        owner_type="tenant",
        owner_id=str(tenant_a.tenant_id),
    )
    tenant_before = wallet_available(tenant_wallet)
    await billing.record_run_usage(
        tenant_id=tenant_a.tenant_id,
        user_id="atlas-scheduler",
        run_id="sched-1",
        payload={"metrics": {"input_tokens": 1000, "output_tokens": 1000}},
        scheduler=True,
    )
    user_wallet = await billing.get_wallet(
        tenant_id=tenant_a.tenant_id,
        owner_type="user",
        owner_id="atlas-scheduler",
    )
    tenant_wallet = await billing.get_wallet(
        tenant_id=tenant_a.tenant_id,
        owner_type="tenant",
        owner_id=str(tenant_a.tenant_id),
    )
    assert wallet_available(user_wallet) == user_before
    assert wallet_available(tenant_wallet) < tenant_before
