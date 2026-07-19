import { redirect } from "next/navigation";

import { createAgent } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";
import { slugifyName } from "@/lib/validation/agent-form";

export default async function NewAgentPage() {
  const created = await createAgent(await getServerAgentOsToken(), {
    name: "Untitled agent",
    slug: slugifyName(`untitled-${Date.now()}`),
  });
  redirect(`/admin/agents/${created.id}`);
}
