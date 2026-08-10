"use client";

import { useEffect, useMemo, useState } from "react";

import { GrantCreditsForm } from "@/components/billing/GrantCreditsForm";
import { Label, Select } from "@/components/ui/Field";
import {
  getPlatformTenantWallet,
  grantPlatformTenantCredits,
} from "@/lib/api/admin";
import type { PlatformTenant, PlatformTenantWallet } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";

export function PlatformGrantCreditsPanel({
  tenants,
}: {
  tenants: PlatformTenant[];
}) {
  const { getAccessToken } = useAgentOsToken();
  const activeTenants = useMemo(
    () => tenants.filter((tenant) => tenant.isActive),
    [tenants],
  );
  const [tenantId, setTenantId] = useState(activeTenants[0]?.id ?? "");
  const [wallet, setWallet] = useState<PlatformTenantWallet | null>(null);
  const [loadingWallet, setLoadingWallet] = useState(false);
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);

  useEffect(() => {
    if (!tenantId) {
      setWallet(null);
      return;
    }
    let cancelled = false;
    setLoadingWallet(true);
    void (async () => {
      try {
        const row = await getPlatformTenantWallet(await getAccessToken(), tenantId);
        if (!cancelled) {
          setWallet(row);
        }
      } catch {
        if (!cancelled) setWallet(null);
      } finally {
        if (!cancelled) setLoadingWallet(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getAccessToken, tenantId]);

  async function onGrant(credits: number, description: string) {
    if (!tenantId) {
      throw new Error("Select an organization");
    }
    setBusy(true);
    setBanner(null);
    try {
      const updated = await grantPlatformTenantCredits(
        await getAccessToken(),
        tenantId,
        { credits, description },
      );
      setWallet(updated);
      setBanner(
        `Granted ${credits.toLocaleString()} credits to ${activeTenants.find((t) => t.id === tenantId)?.name ?? "organization"}`,
      );
    } catch (reason) {
      throw reason instanceof Error ? reason : new Error("Grant failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-xl border border-line bg-raised/40 p-5">
      <h2 className="font-display text-2xl font-semibold">Grant free credits to an organization</h2>
      <p className="mt-1 max-w-2xl text-sm text-slate-muted">
        Add prepaid credits to a customer organization&apos;s wallet at no charge.
        Org admins use this balance for runs and can grant credits to their users.
      </p>
      {banner ? (
        <p className="mt-3 rounded-md border border-teal/30 bg-teal/10 px-3 py-2 text-sm">
          {banner}
        </p>
      ) : null}
      <div className="mt-4 max-w-md">
        <Label htmlFor="grant-tenant">Organization</Label>
        <Select
          id="grant-tenant"
          value={tenantId}
          onChange={(event) => setTenantId(event.target.value)}
        >
          {activeTenants.length === 0 ? (
            <option value="">No active tenants</option>
          ) : (
            activeTenants.map((tenant) => (
              <option key={tenant.id} value={tenant.id}>
                {tenant.name} (/{tenant.slug})
              </option>
            ))
          )}
        </Select>
      </div>
      <div className="mt-4">
        <GrantCreditsForm
          label="Grant credits"
          hint="Credits are added immediately — no payment or checkout."
          balanceLabel="Organization balance"
          availableCredits={wallet?.availableCredits ?? null}
          balanceLoading={loadingWallet}
          defaultCredits="50000"
          defaultDescription="Free credits from platform admin"
          presets={[10_000, 25_000, 50_000, 100_000]}
          busy={busy}
          disabled={!tenantId || activeTenants.length === 0}
          onGrant={onGrant}
        />
      </div>
    </section>
  );
}
