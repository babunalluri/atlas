import { KnowledgeList } from "@/components/knowledge/KnowledgeList";
import {
  listIngestionStatuses,
  listKnowledgeBases,
} from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function KnowledgePage() {
  const token = await getServerAgentOsToken();
  const [sources, bases] = await Promise.all([
    listIngestionStatuses(token),
    listKnowledgeBases(token),
  ]);
  return <KnowledgeList bases={bases} sources={sources} />;
}
