import { TracesList } from "@/components/traces/TracesList";
import { buildActivityRows } from "@/lib/activities";
import {
  listAdminSessions,
  listAgents,
  listSchedules,
  listTeams,
  listWorkflows,
} from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function TracesPage() {
  const token = await getServerAgentOsToken();
  const [sessions, schedules, agents, teams, workflows] = await Promise.all([
    listAdminSessions(token),
    listSchedules(token).catch(() => []),
    listAgents(token).catch(() => []),
    listTeams(token).catch(() => []),
    listWorkflows(token).catch(() => []),
  ]);

  const activities = buildActivityRows({
    sessions,
    schedules,
    agents,
    teams,
    workflows,
  });

  return <TracesList initialActivities={activities} />;
}
