import { notFound } from "next/navigation";

import { auth } from "@/auth";
import { WorkflowChooser } from "@/components/chat/WorkflowChooser";
import { redirect } from "@/i18n/navigation";
import { getPublicTenantBranding } from "@/lib/api/admin";

/**
 * Tenant home.
 *
 * Trading desks are instrument-first, so this redirects to the trader
 * workspace — the instrument list. That has to happen here, not only in the
 * post-login resolvers: middleware sets `callbackUrl` to whatever path you were
 * on, so anyone signing in from a bookmarked /chat would otherwise land back on
 * the three-tab desk. The three-tab desk now lives at /t/{slug}/desk.
 */
export default async function TenantHome({
  params,
}: {
  params: Promise<{ tenantSlug: string; locale: string }>;
}) {
  const { tenantSlug, locale } = await params;
  const session = await auth();
  let tenant;
  try {
    tenant = await getPublicTenantBranding(tenantSlug);
  } catch {
    notFound();
  }
  if (tenant.domain === "stock_broker") {
    redirect({ href: `/t/${tenantSlug}/workspace`, locale });
  }
  return <WorkflowChooser tenant={tenant} serverSession={session} />;
}
