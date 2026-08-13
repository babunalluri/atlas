import { DomainWorkspacePage } from "@/components/domains/DomainWorkspacePage";
import { getDomainDashboard } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function MetricsPage() {
  const data = await getDomainDashboard(await getServerAgentOsToken());
  return <DomainWorkspacePage initialData={data} />;
}
