import { TeamEditor } from "@/components/team-builder/TeamEditor";
import { getTeam, listAgents } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export default async function TeamEditorPage({
  params,
}: {
  params: Promise<{ teamId: string }>;
}) {
  const { teamId } = await params;
  const token = await getServerAgentOsToken();
  const [team, agents] = await Promise.all([
    getTeam(token, teamId),
    listAgents(token),
  ]);
  return <TeamEditor initial={team} agents={agents} />;
}
