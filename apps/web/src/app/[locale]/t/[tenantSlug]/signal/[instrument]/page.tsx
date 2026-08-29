import { notFound } from "next/navigation";

import { auth } from "@/auth";
import { TraderWorkspace } from "@/components/domains/TraderWorkspace";
import { getPublicTenantBranding } from "@/lib/api/admin";

/**
 * Signal Engine in its own window — no Param Chart, no Options Lab.
 *
 * The instrument segment scopes the board: `/state` and `/stream` both carry
 * it as `?instrument=`, the engine warms a matrix row for any watched name,
 * and an unwarmed row renders as "warming" rather than the desk primary's
 * numbers under this instrument's heading.
 */
export default async function SignalWindow({
  params,
}: {
  params: Promise<{ tenantSlug: string; instrument: string }>;
}) {
  const { tenantSlug, instrument } = await params;
  const session = await auth();
  try {
    const tenant = await getPublicTenantBranding(tenantSlug);
    if (tenant.domain !== "stock_broker") notFound();
    return (
      <TraderWorkspace
        tenant={tenant}
        serverSession={session}
        instrument={decodeURIComponent(instrument)}
        surface="signal"
      />
    );
  } catch {
    notFound();
  }
}
