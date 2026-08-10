import { redirect } from "next/navigation";

import { createTeam } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";
import { provisionalSlug } from "@/lib/validation/agent-form";

export default async function NewTeamPage() {
  const created = await createTeam(await getServerAgentOsToken(), {
    name: "Untitled team",
    slug: provisionalSlug("team"),
  });
  redirect(`/admin/teams/${created.id}`);
}
