import { WorkflowList } from "@/components/workflow-builder/WorkflowList";
import { listWorkflowCatalog } from "@/lib/api/admin";
import type { CatalogPage, WorkflowSummary } from "@/lib/api/types";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

const EMPTY: CatalogPage<WorkflowSummary> = {
  items: [],
  total: 0,
  page: 1,
  pageSize: 25,
};

export default async function WorkflowsPage() {
  let initial = EMPTY;
  try {
    initial = await listWorkflowCatalog(await getServerAgentOsToken(), {
      page: 1,
      pageSize: 25,
    });
  } catch {
    initial = EMPTY;
  }

  return <WorkflowList initial={initial} />;
}
