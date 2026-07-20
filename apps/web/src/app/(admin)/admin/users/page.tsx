import { UsersPanel } from "@/components/users/UsersPanel";
import { listTenantUsers, listWorkflows } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function UsersPage() {
  const token = await getServerAgentOsToken();
  const [users, workflows] = await Promise.all([
    listTenantUsers(token),
    listWorkflows(token),
  ]);
  return <UsersPanel initialUsers={users} workflows={workflows} />;
}
