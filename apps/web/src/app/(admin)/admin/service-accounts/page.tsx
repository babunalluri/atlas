import { ServiceAccountsPanel } from "@/components/security/ServiceAccountsPanel";
import { listServiceAccounts } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function ServiceAccountsPage() {
  const accounts = await listServiceAccounts(await getServerAgentOsToken());
  return <ServiceAccountsPanel initial={accounts} />;
}
