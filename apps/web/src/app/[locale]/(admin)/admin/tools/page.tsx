import { ToolList } from "@/components/tool-builder/ToolList";
import { listToolDefinitions } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function ToolsPage() {
  const tools = await listToolDefinitions(await getServerAgentOsToken());
  return <ToolList tools={tools} />;
}
