import { UserList } from "@/components/users/UserList";
import { listTenantUsers } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function UsersPage() {
  const users = await listTenantUsers(await getServerAgentOsToken());
  return <UserList initialUsers={users} />;
}
