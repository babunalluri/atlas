import { notFound } from "next/navigation";

import { auth } from "@/auth";
import { TraderWorkspace } from "@/components/domains/TraderWorkspace";
import { getPublicTenantBranding } from "@/lib/api/admin";

/**
 * Automation for one instrument, in its own window.
 *
 * Paper bots for the instrument in the URL segment. Live placement stays admin-only on the backend.
 *
 * A named route rather than `?tool=bots` so the window is linkable,
 * bookmarkable, and shows what it is in the address bar. Both spellings still
 * work — the query form stays valid on the parent route.
 */
export default async function AutomationWindow({
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
        tool="bots"
      />
    );
  } catch {
    notFound();
  }
}
