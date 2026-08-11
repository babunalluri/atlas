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

const CREATE_FAMILIES = new Set(["api", "python", "mcp"]);

export default async function NewToolPage({
  searchParams,
}: {
  searchParams: Promise<{ kind?: string; family?: string }>;
}) {
  const params = await searchParams;
  const kindParam = params.kind;
  const familyParam = params.family;
  const defaultKind =
    kindParam && CREATE_KINDS.has(kindParam as ToolDefinition["kind"])
      ? (kindParam as ToolDefinition["kind"])
      : undefined;
  const defaultFamily =
    familyParam && CREATE_FAMILIES.has(familyParam) ? familyParam : undefined;
  const credentials = await listCredentials(await getServerAgentOsToken());
  return (
    <ToolEditor
      credentials={credentials}
      defaultKind={defaultKind}
      defaultFamily={defaultFamily}
    />
  );
}
