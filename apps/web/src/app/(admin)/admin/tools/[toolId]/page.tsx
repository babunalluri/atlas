import { ToolEditor } from "@/components/tool-builder/ToolEditor";
import { getToolDefinition, listCredentials } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export default async function ToolEditorPage({
  params,
}: {
  params: Promise<{ toolId: string }>;
}) {
  const { toolId } = await params;
  const token = await getServerAgentOsToken();
  const [tool, credentials] = await Promise.all([
    getToolDefinition(token, toolId),
    listCredentials(token),
  ]);
  return <ToolEditor initial={tool} credentials={credentials} />;
}
