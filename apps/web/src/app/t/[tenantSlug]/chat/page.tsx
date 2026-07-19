import { notFound } from "next/navigation";

import { WorkflowChooser } from "@/components/chat/WorkflowChooser";
import { getPublicTenantBranding } from "@/lib/api/admin";

export default async function WorkflowChatHome({
  params,
}: {
  params: Promise<{ tenantSlug: string }>;
}) {
  const { tenantSlug } = await params;
  try {
    const tenant = await getPublicTenantBranding(tenantSlug);
    return <WorkflowChooser tenant={tenant} />;
  } catch {
    notFound();
  }
}
