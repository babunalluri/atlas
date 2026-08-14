import { notFound } from "next/navigation";

import { auth } from "@/auth";
import { CustomerChat } from "@/components/chat/CustomerChat";
import { getPublicWorkflowSurface } from "@/lib/api/admin";

export default async function CustomerWorkflowPage({
  params,
}: {
  params: Promise<{ tenantSlug: string; workflowSlug: string }>;
}) {
  const { tenantSlug, workflowSlug } = await params;
  const session = await auth();
  try {
    const surface = await getPublicWorkflowSurface(tenantSlug, workflowSlug);
    return <CustomerChat surface={surface} serverSession={session} />;
  } catch {
    notFound();
  }
}
