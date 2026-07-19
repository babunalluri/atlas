import { WorkflowEditor } from "@/components/workflow-builder/WorkflowEditor";
import { getWorkflow, listAgents, listTeams } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export default async function WorkflowEditorPage({
  params,
}: {
  params: Promise<{ workflowId: string }>;
}) {
  const { workflowId } = await params;
  const token = await getServerAgentOsToken();
  const [workflow, agents, teams] = await Promise.all([
    getWorkflow(token, workflowId),
    listAgents(token),
    listTeams(token),
  ]);
  return <WorkflowEditor initial={workflow} agents={agents} teams={teams} />;
}
