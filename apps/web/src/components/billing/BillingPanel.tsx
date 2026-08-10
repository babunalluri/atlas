"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label } from "@/components/ui/Field";
import {
  getTenantBillingWallet,
  grantBillingCredits,
  listTenantBillingLedger,
  listTenantBillingPlans,
  purchaseTenantCreditPack,
  upsertTenantBillingPlan,
} from "@/lib/api/admin";
import type {
  BillingLedgerEntry,
  BillingPlan,
  BillingWallet,
  TenantUser,
} from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";

function formatCredits(value: number): string {
  return value.toLocaleString();
}

export function BillingPanel({
  initialWallet,
  initialPlans,
  initialLedger,
  users,
}: {
  initialWallet: BillingWallet;
  initialPlans: BillingPlan[];
  initialLedger: BillingLedgerEntry[];
  users: TenantUser[];
}) {
  const { getAccessToken } = useAgentOsToken();
  const [wallet, setWallet] = useState(initialWallet);
  const [plans, setPlans] = useState(initialPlans);
  const [ledger, setLedger] = useState(initialLedger);
  const [plan, setPlan] = useState(initialPlans[0] ?? null);
  const [grantUserId, setGrantUserId] = useState(users[0]?.userId ?? "");
  const [grantCredits, setGrantCredits] = useState("1000");
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    const token = await getAccessToken();
    const [nextWallet, nextPlans, nextLedger] = await Promise.all([
      getTenantBillingWallet(token),
      listTenantBillingPlans(token),
      listTenantBillingLedger(token),
    ]);
    setWallet(nextWallet);
    setPlans(nextPlans);
    setLedger(nextLedger);
    setPlan(nextPlans[0] ?? null);
  }

  async function savePlan() {
    if (!plan) return;
    setBusy(true);
    setError(null);
    try {
      const token = await getAccessToken();
      await upsertTenantBillingPlan(token, plan.slug, {
        name: plan.name,
        description: plan.description,
        monthlyPriceCents: plan.monthlyPriceCents,
        includedCreditsMonthly: plan.includedCreditsMonthly,
        creditsPer1kInputTokens: plan.creditsPer1kInputTokens,
        creditsPer1kOutputTokens: plan.creditsPer1kOutputTokens,
        creditPackCredits: plan.creditPackCredits,
        creditPackPriceCents: plan.creditPackPriceCents,
        isActive: plan.isActive,
      });
      setBanner("Plan saved");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save plan");
    } finally {
      setBusy(false);
    }
  }

  async function onGrantUser() {
    const credits = Number.parseInt(grantCredits, 10);
    if (!grantUserId || !Number.isFinite(credits) || credits <= 0) {
      setError("Enter a user and positive credit amount");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const token = await getAccessToken();
      await grantBillingCredits(token, {
        ownerType: "user",
        ownerId: grantUserId,
        credits,
        description: "Admin grant",
      });
      setBanner(`Granted ${formatCredits(credits)} credits`);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Grant failed");
    } finally {
      setBusy(false);
    }
  }

  async function onBuyOrgPack() {
    setBusy(true);
    setError(null);
    try {
      const token = await getAccessToken();
      const result = await purchaseTenantCreditPack(token, {
        planId: plan?.id ?? null,
      });
      if (result.checkoutUrl && result.status === "pending") {
        window.location.href = result.checkoutUrl;
        return;
      }
      setWallet(result.wallet);
      setBanner("Credit pack purchased");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Purchase failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-8 pb-16">
      <header className="space-y-2">
        <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          Configure
        </p>
        <h1 className="font-display text-3xl font-semibold leading-snug py-0.5">
          Billing & credits
        </h1>
        <p className="max-w-2xl text-sm text-muted-foreground">
          Prepaid credits for your organization and end users. Runs hard-stop at
          zero balance. Local dev uses instant dummy checkout; production uses
          Razorpay (INR) when configured.
        </p>
      </header>

      {banner ? (
        <p className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-4 py-2 text-sm text-emerald-700 dark:text-emerald-300">
          {banner}
        </p>
      ) : null}
      {error ? (
        <p className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-2 text-sm text-destructive">
          {error}
        </p>
      ) : null}

      <section className="grid gap-4 md:grid-cols-2">
        <article className="rounded-xl border border-border bg-card p-5 shadow-sm">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold">Organization wallet</h2>
              <p className="text-sm text-muted-foreground">
                Platform wholesale usage debited here on every run.
              </p>
            </div>
            <Badge tone="neutral">{wallet.subscriptionStatus}</Badge>
          </div>
          <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
            <div>
              <dt className="text-muted-foreground">Available</dt>
              <dd className="text-2xl font-semibold tabular-nums">
                {formatCredits(wallet.availableCredits)}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Monthly allowance left</dt>
              <dd className="text-lg font-medium tabular-nums">
                {formatCredits(wallet.allowanceRemaining)}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Balance</dt>
              <dd className="tabular-nums">{formatCredits(wallet.balanceCredits)}</dd>
            </div>
          </dl>
          <Button className="mt-4" disabled={busy} onClick={() => void onBuyOrgPack()}>
            Buy org credit pack
          </Button>
        </article>

        {plan ? (
          <article className="rounded-xl border border-border bg-card p-5 shadow-sm">
            <h2 className="text-lg font-semibold">End-user plan</h2>
            <p className="text-sm text-muted-foreground">
              Rates charged to users on top of org wholesale.
            </p>
            <div className="mt-4 space-y-3">
              <div>
                <Label htmlFor="plan-name">Plan name</Label>
                <Input
                  id="plan-name"
                  value={plan.name}
                  onChange={(e) =>
                    setPlan({ ...plan, name: e.target.value })
                  }
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label htmlFor="plan-allowance">Monthly included credits</Label>
                  <Input
                    id="plan-allowance"
                    type="number"
                    value={plan.includedCreditsMonthly}
                    onChange={(e) =>
                      setPlan({
                        ...plan,
                        includedCreditsMonthly: Number(e.target.value),
                      })
                    }
                  />
                </div>
                <div>
                  <Label htmlFor="plan-pack">Credit pack size</Label>
                  <Input
                    id="plan-pack"
                    type="number"
                    value={plan.creditPackCredits}
                    onChange={(e) =>
                      setPlan({
                        ...plan,
                        creditPackCredits: Number(e.target.value),
                      })
                    }
                  />
                </div>
              </div>
              <Button disabled={busy} onClick={() => void savePlan()}>
                Save plan
              </Button>
            </div>
          </article>
        ) : null}
      </section>

      <section className="rounded-xl border border-border bg-card p-5 shadow-sm">
        <h2 className="text-lg font-semibold">Grant user credits</h2>
        <div className="mt-4 flex flex-wrap items-end gap-3">
          <div className="min-w-[200px] flex-1">
            <Label htmlFor="grant-user">User</Label>
            <select
              id="grant-user"
              className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              value={grantUserId}
              onChange={(e) => setGrantUserId(e.target.value)}
            >
              {users.map((user) => (
                <option key={user.userId} value={user.userId}>
                  {user.displayName || user.email || user.userId}
                </option>
              ))}
            </select>
          </div>
          <div className="w-36">
            <Label htmlFor="grant-amount">Credits</Label>
            <Input
              id="grant-amount"
              value={grantCredits}
              onChange={(e) => setGrantCredits(e.target.value)}
            />
          </div>
          <Button disabled={busy} onClick={() => void onGrantUser()}>
            Grant
          </Button>
        </div>
      </section>

      <section className="rounded-xl border border-border bg-card p-5 shadow-sm">
        <h2 className="text-lg font-semibold">Organization ledger</h2>
        <ul className="mt-4 divide-y divide-border">
          {ledger.length === 0 ? (
            <li className="py-4 text-sm text-muted-foreground">No entries yet.</li>
          ) : (
            ledger.map((entry) => (
              <li
                key={entry.id}
                className="flex flex-wrap items-center justify-between gap-2 py-3 text-sm"
              >
                <div>
                  <p className="font-medium">{entry.description}</p>
                  <p className="text-xs text-muted-foreground">
                    {entry.entryType} · {formatRelative(entry.createdAt)}
                  </p>
                </div>
                <span
                  className={
                    entry.amountCredits >= 0
                      ? "font-medium text-emerald-600 dark:text-emerald-400"
                      : "font-medium text-amber-700 dark:text-amber-300"
                  }
                >
                  {entry.amountCredits >= 0 ? "+" : ""}
                  {formatCredits(entry.amountCredits)}
                </span>
              </li>
            ))
          )}
        </ul>
      </section>
    </div>
  );
}
