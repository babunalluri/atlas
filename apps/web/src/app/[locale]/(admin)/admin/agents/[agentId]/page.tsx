import { AgentEditor } from "@/components/agent-builder/AgentEditor";
import { getAgent, listCredentials, listToolDefinitions } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export default async function AgentEditorPage({
  params,
}: {
  params: Promise<{ agentId: string }>;
}) {
  const { agentId } = await params;
  const token = await getServerAgentOsToken();
  const [agent, toolDefinitions, credentials] = await Promise.all([
    getAgent(token, agentId),
    listToolDefinitions(token),
    listCredentials(token),
  ]);
  return (
    <AgentEditor
      initial={agent}
      toolDefinitions={toolDefinitions}
      credentials={credentials}
    />
  );
}
