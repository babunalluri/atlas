import { DomainWorkspaceDashboard } from "@/components/domains/DomainWorkspaceDashboard";
import { getDomainDashboard } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function MetricsPage() {
  const data = await getDomainDashboard(await getServerAgentOsToken());
  return <DomainWorkspaceDashboard data={data} />;
}
