import { notFound } from "next/navigation";

import { UserEditor } from "@/components/users/UserEditor";
import { getTenantUser, listWorkflows } from "@/lib/api/admin";
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
    const [user, workflows] = await Promise.all([
      getTenantUser(token, userId),
      listWorkflows(token),
    ]);
    return <UserEditor mode="edit" initial={user} workflows={workflows} />;
  } catch {
    notFound();
  }
}
