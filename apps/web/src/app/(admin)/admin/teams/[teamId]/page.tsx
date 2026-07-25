import { TeamEditor } from "@/components/team-builder/TeamEditor";
import {
  getTeam,
  listAgents,
  listCredentials,
  listToolDefinitions,
} from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export default async function TeamEditorPage({
  params,
}: {
  params: Promise<{ teamId: string }>;
}) {
  const { teamId } = await params;
  const token = await getServerAgentOsToken();
  const [team, agents, toolDefinitions, credentials] = await Promise.all([
    getTeam(token, teamId),
    listAgents(token),
    listToolDefinitions(token),
    listCredentials(token),
  ]);
  return (
    <TeamEditor
      initial={team}
      agents={agents}
      toolDefinitions={toolDefinitions}
      credentials={credentials}
    />
  );
}
