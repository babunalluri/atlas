import { CustomerChat } from "@/components/chat/CustomerChat";
import { getPublicWorkflowSurface } from "@/lib/api/admin";
import { notFound } from "next/navigation";

export default async function EmbedWorkflowChatPage({
  params,
}: {
  params: Promise<{ tenantSlug: string; workflowSlug: string }>;
}) {
  const { tenantSlug, workflowSlug } = await params;
  let surface;
  try {
    surface = await getPublicWorkflowSurface(tenantSlug, workflowSlug);
  } catch {
    notFound();
  }
  return <CustomerChat surface={surface} embedded />;
}
