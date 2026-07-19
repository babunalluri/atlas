import { notFound } from "next/navigation";

import { CustomerChat } from "@/components/chat/CustomerChat";
import { getPublicWorkflowSurface } from "@/lib/api/admin";

export default async function CustomerWorkflowPage({
  params,
}: {
  params: Promise<{ tenantSlug: string; workflowSlug: string }>;
}) {
  const { tenantSlug, workflowSlug } = await params;
  try {
    const surface = await getPublicWorkflowSurface(tenantSlug, workflowSlug);
    return <CustomerChat surface={surface} />;
  } catch {
    notFound();
  }
}
