import { WorkflowList } from "@/components/workflow-builder/WorkflowList";
import { listWorkflows } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export default async function WorkflowsPage() {
  const workflows = await listWorkflows(await getServerAgentOsToken());
  return <WorkflowList workflows={workflows} />;
}
