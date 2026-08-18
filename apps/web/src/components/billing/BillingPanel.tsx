"use client";

import { useEffect, useMemo, useState } from "react";

import { Link } from "@/i18n/navigation";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { PlusIcon, SaveIcon } from "@/components/ui/icons";
import { Input, Label } from "@/components/ui/Field";
import { SearchableSelect } from "@/components/ui/SearchableSelect";
import { GrantCreditsForm } from "@/components/billing/GrantCreditsForm";
import {
  getTenantBillingWallet,
  getTenantUserBillingWallet,
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

function userLabel(user: TenantUser): string {
  const name = user.displayName?.trim();
  const email = user.email?.trim();
  if (name && email) return `${name} (${email})`;
  return name || email || user.userId;
}

export function BillingPanel({
  initialWallet,
  initialPlans,
  initialLedger,
  users,
  prefillUserId = null,
}: {
  initialWallet: BillingWallet;
  initialPlans: BillingPlan[];
  initialLedger: BillingLedgerEntry[];
  users: TenantUser[];
  prefillUserId?: string | null;
}) {
  const { getAccessToken } = useAgentOsToken();
  const selectableUsers = useMemo(
    () =>
      users.filter(
        (user) =>
          user.isActive &&
          user.userId &&
          !user.userId.startsWith("invite:") &&
          !user.invitePending,
      ),
    [users],
  );
  const [wallet, setWallet] = useState(initialWallet);
  const [plans, setPlans] = useState(initialPlans);
  const [ledger, setLedger] = useState(initialLedger);
  const [plan, setPlan] = useState(initialPlans[0] ?? null);
  const [grantUserId, setGrantUserId] = useState(
    prefillUserId && selectableUsers.some((u) => u.userId === prefillUserId)
      ? prefillUserId
      : selectableUsers[0]?.userId ?? "",
  );
  const selectedUser = useMemo(
    () => selectableUsers.find((user) => user.userId === grantUserId) ?? null,
    [grantUserId, selectableUsers],
  );
  const [userWalletCredits, setUserWalletCredits] = useState<number | null>(null);
  const [userWalletLoading, setUserWalletLoading] = useState(false);
  const [grantBusy, setGrantBusy] = useState(false);
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!prefillUserId) return;
    const node = document.getElementById("grant-credits");
    node?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [prefillUserId]);

  useEffect(() => {
    if (!grantUserId) {
      setUserWalletCredits(null);
      return;
    }
    let cancelled = false;
    setUserWalletLoading(true);
    void (async () => {
      try {
        const row = await getTenantUserBillingWallet(
          await getAccessToken(),
          grantUserId,
        );
        if (!cancelled) setUserWalletCredits(row.availableCredits);
      } catch {
        if (!cancelled) setUserWalletCredits(null);
      } finally {
        if (!cancelled) setUserWalletLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getAccessToken, grantUserId]);

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

  async function onGrantUser(credits: number, description: string) {
    if (!grantUserId) {
      throw new Error("Select a user first");
    }
    setGrantBusy(true);
    setError(null);
    try {
      const token = await getAccessToken();
      const updated = await grantBillingCredits(token, {
        ownerType: "user",
        ownerId: grantUserId,
        credits,
        description,
      });
      setUserWalletCredits(updated.availableCredits);
      const who = selectedUser ? userLabel(selectedUser) : "user";
      setBanner(`Granted ${formatCredits(credits)} credits to ${who}`);
      await refresh();
    } catch (reason) {
      throw reason instanceof Error ? reason : new Error("Grant failed");
    } finally {
      setGrantBusy(false);
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
        <p className="text-xs font-medium uppercase tracking-wider text-slate-muted">
          Configure
        </p>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="font-display text-3xl font-semibold leading-snug py-0.5">
              Billing & credits
            </h1>
            <p className="mt-1 max-w-2xl text-sm text-slate-muted">
              Give users free credits, monitor your organization balance, and
              configure usage rates. Runs stop when a wallet hits zero.
            </p>
          </div>
          <Link
            href="/admin/users"
            className="text-xs font-medium text-slate-muted hover:text-ink"
          >
            Manage users
          </Link>
        </div>
      </header>

      {banner ? (
        <p className="rounded-lg border border-teal/30 bg-teal/10 px-4 py-2 text-sm text-teal">
          {banner}
        </p>
      ) : null}
      {error ? (
        <p className="rounded-lg border border-rose/30 bg-rose/10 px-4 py-2 text-sm text-rose">
          {error}
        </p>
      ) : null}

      <section
        id="grant-credits"
        className="rounded-xl border border-line bg-raised/40 p-5 scroll-mt-6"
      >
        <h2 className="font-display text-xl font-semibold">Grant free credits to a user</h2>
        <p className="mt-1 text-sm text-slate-muted">
          Top up someone&apos;s wallet instantly — no payment required. They need
          credits before they can run workflows or chat.
        </p>
        {prefillUserId && selectedUser ? (
          <p className="mt-3 rounded-md border border-teal/20 bg-teal/5 px-3 py-2 text-sm">
            Granting credits to{" "}
            <span className="font-medium text-ink">{userLabel(selectedUser)}</span>
            {prefillUserId === grantUserId ? (
              <Link
                href={`/admin/users/${encodeURIComponent(grantUserId)}`}
                className="ml-2 text-xs font-medium text-teal hover:underline"
              >
                Back to user
              </Link>
            ) : null}
          </p>
        ) : null}
        <div className="mt-4 max-w-lg">
          <Label htmlFor="grant-user">User</Label>
          <SearchableSelect
            id="grant-user"
            value={grantUserId}
            onChange={setGrantUserId}
            disabled={selectableUsers.length === 0}
            placeholder={
              selectableUsers.length === 0
                ? "No active users — create one first"
                : "Search users…"
            }
            emptyMessage="No matching users"
            options={selectableUsers.map((user) => ({
              value: user.userId,
              label: userLabel(user),
            }))}
          />
        </div>
        <div className="mt-4">
          <GrantCreditsForm
            label="Grant credits"
            hint="Credits appear in the user's wallet immediately. User grants are not listed in the organization ledger below."
            balanceLabel="This user's balance"
            availableCredits={userWalletCredits}
            balanceLoading={userWalletLoading}
            defaultCredits="1000"
            defaultDescription="Free credits from org admin"
            presets={[500, 1_000, 5_000, 10_000]}
            busy={grantBusy}
            disabled={selectableUsers.length === 0 || !grantUserId}
            onGrant={onGrantUser}
          />
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        <article className="rounded-xl border border-line bg-raised/40 p-5">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold">Organization wallet</h2>
              <p className="text-sm text-slate-muted">
                Your org&apos;s balance for platform usage on every run.
              </p>
            </div>
            <Badge tone="neutral">{wallet.subscriptionStatus}</Badge>
          </div>
          <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
            <div>
              <dt className="text-slate-muted">Available</dt>
              <dd className="text-2xl font-semibold tabular-nums">
                {formatCredits(wallet.availableCredits)}
              </dd>
            </div>
            <div>
              <dt className="text-slate-muted">Monthly allowance left</dt>
              <dd className="text-lg font-medium tabular-nums">
                {formatCredits(wallet.allowanceRemaining)}
              </dd>
            </div>
            <div>
              <dt className="text-slate-muted">Purchased balance</dt>
              <dd className="tabular-nums">{formatCredits(wallet.balanceCredits)}</dd>
            </div>
          </dl>
          <Button
            className="mt-4"
            icon={<PlusIcon />}
            disabled={busy}
            onClick={() => void onBuyOrgPack()}
          >
            Buy credit pack
          </Button>
        </article>

        {plan ? (
          <article className="rounded-xl border border-line bg-raised/40 p-5">
            <h2 className="text-lg font-semibold">End-user plan</h2>
            <p className="text-sm text-slate-muted">
              Usage rates charged to users when they run agents.
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
              <Button icon={<SaveIcon />} disabled={busy} onClick={() => void savePlan()}>
                Save plan
              </Button>
            </div>
          </article>
        ) : null}
      </section>

      <section className="rounded-xl border border-line bg-raised/40 p-5">
        <h2 className="text-lg font-semibold">Organization ledger</h2>
        <p className="mt-1 text-sm text-slate-muted">
          Platform usage and org-level grants. To audit a user&apos;s credits, grant
          from their profile or check their balance above.
        </p>
        <ul className="mt-4 divide-y divide-line">
          {ledger.length === 0 ? (
            <li className="py-4 text-sm text-slate-muted">No entries yet.</li>
          ) : (
            ledger.map((entry) => (
              <li
                key={entry.id}
                className="flex flex-wrap items-center justify-between gap-2 py-3 text-sm"
              >
                <div>
                  <p className="font-medium">{entry.description}</p>
                  <p className="text-xs text-slate-muted">
                    {entry.entryType} · {formatRelative(entry.createdAt)}
                  </p>
                </div>
                <span
                  className={
                    entry.amountCredits >= 0
                      ? "font-medium text-teal"
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
