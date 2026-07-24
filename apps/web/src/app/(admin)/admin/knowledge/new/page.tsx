import { redirect } from "next/navigation";

import { createKnowledgeBase } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export default async function NewKnowledgePage() {
  const created = await createKnowledgeBase(
    await getServerAgentOsToken(),
    "Untitled knowledge",
  );
  redirect(`/admin/knowledge/${created.id}`);
}
