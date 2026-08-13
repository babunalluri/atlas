import { notFound } from "next/navigation";

import { UserEditor } from "@/components/users/UserEditor";
import { getTenantUser, listTeams, listTenantUsers, listWorkflows } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function UserDetailPage({
  params,
}: {
  params: Promise<{ userId: string }>;
}) {
  const { userId } = await params;
  const token = await getServerAgentOsToken();
  try {
    const [user, workflows, teams, users] = await Promise.all([
      getTenantUser(token, userId),
      listWorkflows(token),
      listTeams(token),
      listTenantUsers(token),
    ]);
    return (
      <UserEditor
        mode="edit"
        initial={user}
        workflows={workflows}
        teams={teams}
        takenEmails={users
          .filter((item) => item.id !== user.id)
          .map((item) => item.email)
          .filter((email): email is string => Boolean(email))}
      />
    );
  } catch {
    notFound();
  }
}
