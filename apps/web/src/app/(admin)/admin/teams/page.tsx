import { TeamList } from "@/components/team-builder/TeamList";
import { listTeams } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export default async function TeamsPage() {
  const teams = await listTeams(await getServerAgentOsToken());
  return <TeamList teams={teams} />;
}
