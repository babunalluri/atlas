import { ApprovalsPanel } from "@/components/agent-builder/ApprovalsPanel";
import { listApprovals } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export default async function ApprovalsPage() {
  const approvals = await listApprovals(await getServerAgentOsToken());
  return <ApprovalsPanel initial={approvals} />;
}
