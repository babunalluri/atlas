import { redirect } from "@/i18n/navigation";

import { createWorkflow } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";
import { provisionalSlug } from "@/lib/validation/agent-form";

export default async function NewWorkflowPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  const created = await createWorkflow(await getServerAgentOsToken(), {
    name: "Untitled workflow",
    slug: provisionalSlug("workflow"),
  });
  redirect({ href: `/admin/workflows/${created.id}`, locale });
}
