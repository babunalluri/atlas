import { UserEditor } from "@/components/users/UserEditor";
import { listWorkflows } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function NewUserPage({
  searchParams,
}: {
  searchParams: Promise<{ name?: string }>;
}) {
  const workflows = await listWorkflows(await getServerAgentOsToken());
  const params = await searchParams;

  return (
    <UserEditor
      mode="create"
      workflows={workflows}
      defaultDisplayName={params.name?.trim() ?? ""}
    />
  );
}
