import { ChannelBindingsPanel } from "@/components/integrations/ChannelBindingsPanel";
import { ToolkitIntegrations } from "@/components/integrations/ToolkitIntegrations";
import {
  getWorkspaceInfo,
  listChannelBindings,
  listCredentials,
  listTeamCatalog,
  listToolDefinitions,
  listToolkitCatalog,
  listWorkflowCatalog,
} from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function IntegrationsPage() {
  const token = await getServerAgentOsToken();
  const [catalog, credentials, tools, bindings, teamsPage, workflowsPage, workspace] =
    await Promise.all([
      listToolkitCatalog(token),
      listCredentials(token),
      listToolDefinitions(token),
      listChannelBindings(token).catch(() => []),
      listTeamCatalog(token, { page: 1, pageSize: 100 }).catch(() => ({
        items: [] as Array<{ id: string; name: string; slug: string }>,
      })),
      listWorkflowCatalog(token, { page: 1, pageSize: 100 }).catch(() => ({
        items: [] as Array<{ id: string; name: string; slug: string }>,
      })),
      getWorkspaceInfo(token).catch(() => null),
    ]);
  return (
    <div className="space-y-4">
      <ChannelBindingsPanel
        initialBindings={bindings}
        credentials={credentials}
        teams={teamsPage.items.map((item) => ({
          id: item.id,
          name: item.name,
          slug: item.slug,
        }))}
        workflows={workflowsPage.items.map((item) => ({
          id: item.id,
          name: item.name,
          slug: item.slug,
        }))}
        tenantSlug={workspace?.slug ?? "workspace"}
      />
      <ToolkitIntegrations
        catalog={catalog}
        initialCredentials={credentials}
        initialTools={tools}
      />
    </div>
  );
}
