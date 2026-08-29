import { notFound } from "next/navigation";

import { redirect } from "@/i18n/navigation";
import { getPublicTenantBranding } from "@/lib/api/admin";

/**
 * Legacy route — the three-tab desk (Signal · Param Chart · Options Lab) is gone.
 *
 * It mounted Signal and Param Chart hidden to keep their SSE alive, which is
 * exactly the load that starves a single-worker backend. Traders and operators
 * both land on the instrument-first workspace and open one tool per window.
 */
export default async function DeskPage({
  params,
}: {
  params: Promise<{ locale: string; tenantSlug: string }>;
}) {
  const { locale, tenantSlug } = await params;
  try {
    const tenant = await getPublicTenantBranding(tenantSlug);
    if (tenant.domain !== "stock_broker") notFound();
  } catch {
    notFound();
  }
  redirect({ href: `/t/${tenantSlug}/workspace`, locale });
}
