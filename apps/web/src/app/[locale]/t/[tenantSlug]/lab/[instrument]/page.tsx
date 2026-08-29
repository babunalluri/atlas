import { notFound } from "next/navigation";

import { auth } from "@/auth";
import { TraderWorkspace } from "@/components/domains/TraderWorkspace";
import { getPublicTenantBranding } from "@/lib/api/admin";

/**
 * One instrument, one window (product rule).
 *
 * The instrument segment drives the live chain — `getOptionsChain` and
 * `streamOptionsChain` send it as `?underlying=` (E1). `?tool=` selects which
 * tool the landing sent us to: chain, ideas, backtest, or bots.
 */
export default async function LabInstrument({
  params,
  searchParams,
}: {
  params: Promise<{ tenantSlug: string; instrument: string }>;
  searchParams: Promise<{ tool?: string }>;
}) {
  const { tenantSlug, instrument } = await params;
  const { tool } = await searchParams;
  const session = await auth();
  try {
    const tenant = await getPublicTenantBranding(tenantSlug);
    if (tenant.domain !== "stock_broker") notFound();
    return (
      <TraderWorkspace
        tenant={tenant}
        serverSession={session}
        instrument={decodeURIComponent(instrument)}
        tool={tool ?? null}
      />
    );
  } catch {
    notFound();
  }
}
