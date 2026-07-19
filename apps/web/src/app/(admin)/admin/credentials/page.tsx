import { CredentialsPanel } from "@/components/agent-builder/CredentialsPanel";
import { listCredentials, listToolkitCatalog } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function CredentialsPage() {
  const token = await getServerAgentOsToken();
  const [credentials, toolkitCatalog] = await Promise.all([
    listCredentials(token),
    listToolkitCatalog(token),
  ]);
  const toolkitProviders = Array.from(
    new Set(
      toolkitCatalog.flatMap((toolkit) =>
        toolkit.credentials.map((credential) => credential.provider),
      ),
    ),
  ).sort();
  return (
    <CredentialsPanel
      initial={credentials}
      providerOptions={Array.from(
        new Set([
          "openai",
          "anthropic",
          "groq",
          "rest_api",
          ...toolkitProviders,
        ]),
      )}
    />
  );
}
