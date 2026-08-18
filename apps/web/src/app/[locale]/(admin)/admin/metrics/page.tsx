import { DomainMetricsPage } from "@/components/domains/DomainMetricsPage";
import { getDomainDashboard } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function MetricsPage() {
  const data = await getDomainDashboard(await getServerAgentOsToken());
  return (
    <div className="h-full overflow-y-auto px-5 py-8">
      <DomainMetricsPage initialData={data} />
    </div>
  );
}
