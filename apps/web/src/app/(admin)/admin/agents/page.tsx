import { AgentList } from "@/components/agent-builder/AgentList";
import { listAgentCatalog } from "@/lib/api/admin";
import type { CatalogPage, AgentSummary } from "@/lib/api/types";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

const EMPTY: CatalogPage<AgentSummary> = {
  items: [],
  total: 0,
  page: 1,
  pageSize: 25,
};

export default async function AgentsPage() {
  let initial = EMPTY;
  try {
    initial = await listAgentCatalog(await getServerAgentOsToken(), {
      page: 1,
      pageSize: 25,
    });
  } catch {
    initial = EMPTY;
  }

  return <AgentList initial={initial} />;
}
