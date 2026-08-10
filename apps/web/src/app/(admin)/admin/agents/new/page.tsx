import { redirect } from "next/navigation";

import { createAgent } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";
import { provisionalSlug } from "@/lib/validation/agent-form";

export default async function NewAgentPage() {
  const created = await createAgent(await getServerAgentOsToken(), {
    name: "Untitled agent",
    slug: provisionalSlug("agent"),
  });
  redirect(`/admin/agents/${created.id}`);
}
