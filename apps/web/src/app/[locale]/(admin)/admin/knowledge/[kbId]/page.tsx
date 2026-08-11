import { notFound } from "next/navigation";

import { KnowledgeEditor } from "@/components/knowledge/KnowledgeEditor";
import {
  listKnowledgeBases,
  listKnowledgeSources,
} from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function KnowledgeDetailPage({
  params,
}: {
  params: Promise<{ kbId: string }>;
}) {
  const { kbId } = await params;
  const token = await getServerAgentOsToken();
  const bases = await listKnowledgeBases(token);
  const base = bases.find((item) => item.id === kbId);
  if (!base) notFound();

  const sources = await listKnowledgeSources(token, kbId);
  return (
    <KnowledgeEditor knowledgeBase={base} initialSources={sources} />
  );
}
