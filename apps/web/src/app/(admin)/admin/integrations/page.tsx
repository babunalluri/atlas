import { ToolkitIntegrations } from "@/components/integrations/ToolkitIntegrations";
import {
  listCredentials,
  listCustomPythonCatalog,
  listToolDefinitions,
  listToolkitCatalog,
} from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function IntegrationsPage() {
  const token = await getServerAgentOsToken();
  const [catalog, credentials, tools, customPythonCatalog] = await Promise.all([
    listToolkitCatalog(token),
    listCredentials(token),
    listToolDefinitions(token),
    listCustomPythonCatalog(token),
  ]);
  return (
    <ToolkitIntegrations
      catalog={catalog}
      initialCredentials={credentials}
      initialTools={tools}
      customPythonCatalog={customPythonCatalog}
    />
  );
}
