import { TeamList } from "@/components/team-builder/TeamList";
import { listTeamCatalog } from "@/lib/api/admin";
import type { CatalogPage, TeamSummary } from "@/lib/api/types";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

const EMPTY: CatalogPage<TeamSummary> = {
  items: [],
  total: 0,
  page: 1,
  pageSize: 25,
};

export default async function TeamsPage() {
  let initial = EMPTY;
  try {
    initial = await listTeamCatalog(await getServerAgentOsToken(), {
      page: 1,
      pageSize: 25,
    });
  } catch {
    initial = EMPTY;
  }

  return <TeamList initial={initial} />;
}
