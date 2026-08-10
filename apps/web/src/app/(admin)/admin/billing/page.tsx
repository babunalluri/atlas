import { BillingPanel } from "@/components/billing/BillingPanel";
import {
  getTenantBillingWallet,
  listTenantBillingLedger,
  listTenantBillingPlans,
  listTenantUsers,
} from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function BillingPage() {
  const token = await getServerAgentOsToken();
  const [wallet, plans, ledger, users] = await Promise.all([
    getTenantBillingWallet(token),
    listTenantBillingPlans(token),
    listTenantBillingLedger(token),
    listTenantUsers(token),
  ]);
  return (
    <BillingPanel
      initialWallet={wallet}
      initialPlans={plans}
      initialLedger={ledger}
      users={users}
    />
  );
}
