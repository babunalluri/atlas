import { redirect } from "@/i18n/navigation";

import { createKnowledgeBase } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export default async function NewKnowledgePage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  const created = await createKnowledgeBase(
    await getServerAgentOsToken(),
    "Untitled knowledge",
  );
  redirect({ href: `/admin/knowledge/${created.id}`, locale });
}
