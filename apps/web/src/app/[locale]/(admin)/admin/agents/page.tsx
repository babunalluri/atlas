import { Suspense } from "react";

import { AgentList } from "@/components/agent-builder/AgentList";
import { AdminPageSkeleton } from "@/components/layout/AdminPageSkeleton";
import { listAgentCatalog } from "@/lib/api/admin";
import type { CatalogPage, AgentSummary } from "@/lib/api/types";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

const EMPTY: CatalogPage<AgentSummary> = {
  items: [],
  total: 0,
  page: 1,
  pageSize: 25,
};

async function AgentsData() {
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

export default function AgentsPage() {
  return (
    <Suspense fallback={<AdminPageSkeleton />}>
      <AgentsData />
    </Suspense>
  );
}
