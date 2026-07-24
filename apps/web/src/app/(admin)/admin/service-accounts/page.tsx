import { ServiceAccountList } from "@/components/security/ServiceAccountList";
import { listServiceAccounts } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function ServiceAccountsPage() {
  const accounts = await listServiceAccounts(await getServerAgentOsToken());
  return <ServiceAccountList initial={accounts} />;
}
