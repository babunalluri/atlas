import { NotificationsPanel } from "@/components/notifications/NotificationsPanel";
import { listSentNotifications, listTenantUsers } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function NotificationsPage({
  searchParams,
}: {
  searchParams: Promise<{ userId?: string }>;
}) {
  const token = await getServerAgentOsToken();
  const params = await searchParams;
  const [users, sent] = await Promise.all([
    listTenantUsers(token),
    listSentNotifications(token),
  ]);
  return (
    <NotificationsPanel
      users={users}
      initialSent={sent}
      prefillUserId={params.userId ?? null}
    />
  );
}
