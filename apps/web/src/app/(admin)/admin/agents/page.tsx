import { AgentList } from "@/components/agent-builder/AgentList";
import { listAgents } from "@/lib/api/admin";
import { MOCK_AGENTS } from "@/lib/api/mocks";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export default async function AgentsPage() {
  let agents = MOCK_AGENTS;
  try {
    agents = await listAgents(await getServerAgentOsToken());
  } catch {
    agents = MOCK_AGENTS;
  }

  return <AgentList agents={agents} />;
}
