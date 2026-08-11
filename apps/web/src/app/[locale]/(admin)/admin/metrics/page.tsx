import { MetricsDashboard } from "@/components/metrics/MetricsDashboard";
import { getMetrics } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function MetricsPage() {
  const data = await getMetrics(await getServerAgentOsToken());
  return <MetricsDashboard data={data} />;
}
