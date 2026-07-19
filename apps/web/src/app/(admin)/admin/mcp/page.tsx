import { McpServerPanel } from "@/components/security/McpServerPanel";
import { getMcpServerSettings } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function McpServerPage() {
  const settings = await getMcpServerSettings(await getServerAgentOsToken());
  return <McpServerPanel initial={settings} />;
}
