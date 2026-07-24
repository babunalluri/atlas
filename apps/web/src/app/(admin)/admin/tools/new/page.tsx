import { ToolEditor } from "@/components/tool-builder/ToolEditor";
import { listCredentials } from "@/lib/api/admin";
import type { ToolDefinition } from "@/lib/api/types";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

const CREATE_KINDS = new Set<ToolDefinition["kind"]>([
  "http",
  "openapi",
  "tenant_python",
  "mcp",
]);

export default async function NewToolPage({
  searchParams,
}: {
  searchParams: Promise<{ kind?: string }>;
}) {
  const params = await searchParams;
  const kindParam = params.kind;
  const defaultKind =
    kindParam && CREATE_KINDS.has(kindParam as ToolDefinition["kind"])
      ? (kindParam as ToolDefinition["kind"])
      : "http";
  const credentials = await listCredentials(await getServerAgentOsToken());
  return <ToolEditor credentials={credentials} defaultKind={defaultKind} />;
}
