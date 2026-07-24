import { redirect } from "next/navigation";

import { createWorkflow } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";
import { slugifyName } from "@/lib/validation/agent-form";

export default async function NewWorkflowPage() {
  const created = await createWorkflow(await getServerAgentOsToken(), {
    name: "Untitled workflow",
    slug: slugifyName(`untitled-workflow-${Date.now()}`),
  });
  redirect(`/admin/workflows/${created.id}`);
}
