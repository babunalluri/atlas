import { redirect } from "@/i18n/navigation";

import { createAgent } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";
import { provisionalSlug } from "@/lib/validation/agent-form";

export default async function NewAgentPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  const created = await createAgent(await getServerAgentOsToken(), {
    name: "Untitled agent",
    slug: provisionalSlug("agent"),
  });
  redirect({ href: `/admin/agents/${created.id}`, locale });
}
