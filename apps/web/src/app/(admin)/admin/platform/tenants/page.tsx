import { PlatformTenantsPanel } from "@/components/platform/PlatformTenantsPanel";
import {
  listPlatformAudit,
  listPlatformTenants,
} from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function PlatformTenantsPage() {
  const token = await getServerAgentOsToken();
  const [tenants, audit] = await Promise.all([
    listPlatformTenants(token),
    listPlatformAudit(token),
  ]);
  return <PlatformTenantsPanel initialTenants={tenants} initialAudit={audit} />;
}
