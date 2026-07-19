import { SessionsPanel } from "@/components/agent-builder/SessionsPanel";
import {
  listAdminSessions,
  listUserMemories,
} from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export default async function SessionsPage() {
  const token = await getServerAgentOsToken();
  const sessions = await listAdminSessions(token);
  const users = [...new Set(sessions.map((session) => session.userId))];
  const memories = (
    await Promise.all(users.map((userId) => listUserMemories(token, userId)))
  ).flat();
  return (
    <SessionsPanel initialSessions={sessions} initialMemories={memories} />
  );
}
