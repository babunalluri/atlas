import { redirect } from "@/i18n/navigation";

import { createTeam } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";
import { provisionalSlug } from "@/lib/validation/agent-form";

export default async function NewTeamPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  const created = await createTeam(await getServerAgentOsToken(), {
    name: "Untitled team",
    slug: provisionalSlug("team"),
  });
  redirect({ href: `/admin/teams/${created.id}`, locale });
}
