import { Suspense } from "react";

import { CredentialsPanel } from "@/components/agent-builder/CredentialsPanel";
import { AdminPageSkeleton } from "@/components/layout/AdminPageSkeleton";
import { listCredentials, listToolkitCatalog } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";
import type { ModelProvider } from "@/lib/api/types";

export const dynamic = "force-dynamic";

const LLM_PROVIDERS: ModelProvider[] = [
  "openai",
  "anthropic",
  "groq",
  "moonshot",
  "nvidia",
  "gemini",
];

async function CredentialsData() {
  const token = await getServerAgentOsToken();
  const [credentials, toolkitCatalog] = await Promise.all([
    listCredentials(token),
    listToolkitCatalog(token),
  ]);
  const llmSet = new Set<string>(LLM_PROVIDERS);
  const toolkitProviders = Array.from(
    new Set(
      toolkitCatalog.flatMap((toolkit) =>
        toolkit.credentials.map((credential) => credential.provider),
      ),
    ),
  )
    .filter((provider) => !llmSet.has(provider))
    .sort();
  return (
    <CredentialsPanel
      initial={credentials}
      providerGroups={[
        { label: "LLM", providers: [...LLM_PROVIDERS] },
        {
          label: "Tools / integrations",
          providers: ["rest_api", ...toolkitProviders],
        },
      ]}
    />
  );
}

export default function CredentialsPage() {
  return (
    <Suspense fallback={<AdminPageSkeleton />}>
      <CredentialsData />
    </Suspense>
  );
}
