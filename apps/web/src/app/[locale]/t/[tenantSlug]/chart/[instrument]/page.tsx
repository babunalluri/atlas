import { notFound } from "next/navigation";

import { auth } from "@/auth";
import { TraderWorkspace } from "@/components/domains/TraderWorkspace";
import { getPublicTenantBranding } from "@/lib/api/admin";

/**
 * Param Chart in its own window.
 *
 * The instrument segment is request-scoped: it rides on `?underlying=` for the
 * month read and the SSE overlay, and packs, rebuild locks and the watcher are
 * all keyed by it. Strike and CE/PE are re-derived server-side for a scoped
 * read, so the desk chart does not move when this window opens.
 */
export default async function ChartWindow({
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
        surface="chart"
      />
    );
  } catch {
    notFound();
  }
}
