import { Suspense } from "react";

import { AdminPageSkeleton } from "@/components/layout/AdminPageSkeleton";
import { TracesList } from "@/components/traces/TracesList";
import { buildActivityRows } from "@/lib/activities";
import {
  getWorkspaceInfo,
  listAdminSessions,
  listAgents,
  listSchedules,
  listTeams,
  listWorkflows,
} from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

async function TracesData() {
  const token = await getServerAgentOsToken();
  // listAgents/Teams/Workflows resolve via thin /catalog endpoints (paginated).
  const [sessions, schedules, agents, teams, workflows, workspace] =
    await Promise.all([
      listAdminSessions(token, { limit: 100 }),
      listSchedules(token).catch(() => []),
      listAgents(token).catch(() => []),
      listTeams(token).catch(() => []),
      listWorkflows(token).catch(() => []),
      getWorkspaceInfo(token).catch(() => null),
    ]);

  const activities = buildActivityRows({
    sessions,
    schedules,
    agents,
    teams,
    workflows,
  });

  const timeZone =
    workspace?.timezone || workspace?.tenant_timezone || "UTC";

  return (
    <TracesList initialActivities={activities} timeZone={timeZone} />
  );
}

export default function TracesPage() {
  return (
    <Suspense fallback={<AdminPageSkeleton />}>
      <TracesData />
    </Suspense>
  );
}
