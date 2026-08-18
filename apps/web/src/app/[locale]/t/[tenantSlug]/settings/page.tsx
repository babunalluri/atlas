import { notFound } from "next/navigation";

import { auth } from "@/auth";
import { WorkspaceSettingsPage } from "@/components/chat/WorkspaceSettingsPage";
import { getPublicTenantBranding } from "@/lib/api/admin";

export default async function TenantSettingsPage({
  params,
}: {
  params: Promise<{ tenantSlug: string }>;
}) {
  const { tenantSlug } = await params;
  const session = await auth();
  try {
    const tenant = await getPublicTenantBranding(tenantSlug);
    return <WorkspaceSettingsPage tenant={tenant} serverSession={session} />;
  } catch {
    notFound();
  }
}
