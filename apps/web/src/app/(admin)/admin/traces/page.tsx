import { TraceExplorer } from "@/components/observability/TraceExplorer";
import { getTrace, listTraces } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function TracesPage() {
  const token = await getServerAgentOsToken();
  const traces = await listTraces(token);
  const detail = traces[0] ? await getTrace(token, traces[0].id) : null;
  return <TraceExplorer initialTraces={traces} initialDetail={detail} />;
}
