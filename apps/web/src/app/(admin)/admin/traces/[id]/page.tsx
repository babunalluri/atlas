import { notFound, redirect } from "next/navigation";

import { TracesDetail } from "@/components/traces/TracesDetail";
import { buildActivityRows } from "@/lib/activities";
import {
  getAdminSessionDetail,
  getTrace,
  listAgents,
  listSchedules,
  listTeams,
  listTraces,
  listWorkflows,
} from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

/**
 * Dual-key detail: prefer session id (canonical). If `id` is a legacy trace id,
 * redirect to `/admin/traces/{sessionId}?trace={traceId}`.
 */
export default async function TraceDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ trace?: string }>;
}) {
  const { id } = await params;
  const { trace: preferredTraceId = null } = await searchParams;
  const token = await getServerAgentOsToken();

  const sessionBundle = await getAdminSessionDetail(token, id).catch(
    () => null,
  );

  if (!sessionBundle) {
    const legacyTrace = await getTrace(token, id).catch(() => null);
    if (!legacyTrace?.sessionId) notFound();
    redirect(
      `/admin/traces/${encodeURIComponent(legacyTrace.sessionId)}?trace=${encodeURIComponent(legacyTrace.id)}`,
    );
  }

  const sessionId = sessionBundle.session.id;

  const [schedules, agents, teams, workflows, traces] = await Promise.all([
    listSchedules(token).catch(() => []),
    listAgents(token).catch(() => []),
    listTeams(token).catch(() => []),
    listWorkflows(token).catch(() => []),
    listTraces(token, { sessionId, limit: 50 }).catch(() => []),
  ]);

  const [activity] = buildActivityRows({
    sessions: [sessionBundle.session],
    schedules,
    agents,
    teams,
    workflows,
  });
  if (!activity) notFound();

  const selectedTraceId =
    preferredTraceId && traces.some((row) => row.id === preferredTraceId)
      ? preferredTraceId
      : (traces.find((row) => row.status === "error")?.id ??
        traces[0]?.id ??
        null);
  const initialTrace = selectedTraceId
    ? await getTrace(token, selectedTraceId).catch(() => null)
    : null;

  return (
    <TracesDetail
      activity={activity}
      messages={sessionBundle.messages}
      traces={traces}
      initialTrace={initialTrace}
      preferredTraceId={selectedTraceId}
    />
  );
}
