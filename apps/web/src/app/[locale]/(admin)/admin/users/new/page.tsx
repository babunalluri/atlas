import { UserEditor } from "@/components/users/UserEditor";
import { listTeams, listTenantUsers, listWorkflows } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function NewUserPage({
  searchParams,
}: {
  searchParams: Promise<{ name?: string }>;
}) {
  const token = await getServerAgentOsToken();
  const [workflows, teams, users] = await Promise.all([
    listWorkflows(token),
    listTeams(token),
    listTenantUsers(token),
  ]);
  const params = await searchParams;

  return (
    <UserEditor
      mode="create"
      workflows={workflows}
      teams={teams}
      defaultDisplayName={params.name?.trim() ?? ""}
      takenEmails={users
        .map((user) => user.email)
        .filter((email): email is string => Boolean(email))}
    />
  );
}
