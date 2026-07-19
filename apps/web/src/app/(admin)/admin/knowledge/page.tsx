import { IngestionPanel } from "@/components/agent-builder/IngestionPanel";
import {
  listIngestionStatuses,
  listKnowledgeBases,
} from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export default async function KnowledgePage() {
  const token = await getServerAgentOsToken();
  const [sources, bases] = await Promise.all([
    listIngestionStatuses(token),
    listKnowledgeBases(token),
  ]);
  return <IngestionPanel sources={sources} initialBases={bases} />;
}
