import { notFound } from "next/navigation";

import { auth } from "@/auth";
import { TraderWorkspace } from "@/components/domains/TraderWorkspace";
import { getPublicTenantBranding } from "@/lib/api/admin";

/**
 * Trader workspace landing — pick an instrument, then a tool.
 *
 * Each tool opens in its own window with the instrument in the URL.
 */
export default async function TraderWorkspaceHome({
  params,
}: {
  params: Promise<{ tenantSlug: string }>;
}) {
  const { tenantSlug } = await params;
  const session = await auth();
  try {
    const tenant = await getPublicTenantBranding(tenantSlug);
    if (tenant.domain !== "stock_broker") notFound();
    return <TraderWorkspace tenant={tenant} serverSession={session} />;
  } catch {
    notFound();
  }
}
