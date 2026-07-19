import { CustomerChat } from "@/components/chat/CustomerChat";
import { getPublicChatSurface } from "@/lib/api/admin";
import { ApiError } from "@/lib/agentos/client";
import { notFound } from "next/navigation";

export default async function CustomerChatPage({
  params,
}: {
  params: Promise<{ tenantSlug: string; agentSlug: string }>;
}) {
  const { tenantSlug, agentSlug } = await params;
  const surface = await getPublicChatSurface(tenantSlug, agentSlug).catch((error: unknown) => {
    if (error instanceof ApiError && error.status === 404) {
      notFound();
    }
    throw error;
  });
  return <CustomerChat surface={surface} />;
}
