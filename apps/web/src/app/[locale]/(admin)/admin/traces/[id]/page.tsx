import { notFound } from "next/navigation";

import { redirect } from "@/i18n/navigation";

import { TracesDetail } from "@/components/traces/TracesDetail";
import { buildActivityRows } from "@/lib/activities";
import {
  getAdminSessionDetail,
  getAgent,
  getTeam,
  getTrace,
  getWorkflow,
  getWorkspaceInfo,
  listSchedules,
  listTraces,
} from "@/lib/api/admin";
import type {
  AgentSummary,
  TeamSummary,
  WorkflowSummary,
} from "@/lib/api/types";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

async function resolveTargetSummaries(
  token: string,
  targetType: "agent" | "team" | "workflow",
  targetId: string,
): Promise<{
  agents: AgentSummary[];
  teams: TeamSummary[];
  workflows: WorkflowSummary[];
}> {
  const empty = { agents: [], teams: [], workflows: [] };
  if (!targetId.trim()) return empty;
  try {
    if (targetType === "agent") {
      const agent = await getAgent(token, targetId);
      return {
        ...empty,
        agents: [
          {
            id: agent.id,
            name: agent.name,
            slug: agent.slug,
            status: agent.status,
            model: agent.model,
            updatedAt: agent.updatedAt,
            publishedVersion: agent.publishedVersion,
          },
        ],
      };
    }
    if (targetType === "team") {
      const team = await getTeam(token, targetId);
      return {
        ...empty,
        teams: [
          {
            id: team.id,
            name: team.name,
            slug: team.slug,
            status: team.status,
            mode: team.mode,
            memberCount: team.members.length,
            publishedVersion: team.publishedVersion,
            updatedAt: team.updatedAt,
          },
        ],
      };
    }
    const workflow = await getWorkflow(token, targetId);
    return {
      ...empty,
      workflows: [
        {
          id: workflow.id,
          name: workflow.name,
          slug: workflow.slug,
          mode: workflow.mode,
          status: workflow.status,
          stepCount: workflow.steps.length,
          publishedVersion: workflow.publishedVersion,
          updatedAt: workflow.updatedAt,
        },
      ],
    };
  } catch {
    return empty;
  }
}

/**
 * Dual-key detail: prefer session id (canonical). If `id` is a legacy trace id,
 * redirect to `/admin/traces/{sessionId}?trace={traceId}`.
 */
export default async function TraceDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string; id: string }>;
  searchParams: Promise<{ trace?: string }>;
}) {
  const { locale, id } = await params;
  const { trace: preferredTraceId = null } = await searchParams;
  const token = await getServerAgentOsToken();

  const sessionBundle = await getAdminSessionDetail(token, id).catch(
    () => null,
  );

  if (!sessionBundle) {
    const legacyTrace = await getTrace(token, id).catch(() => null);
    if (!legacyTrace?.sessionId) notFound();
    return redirect({
      href: `/admin/traces/${encodeURIComponent(legacyTrace.sessionId)}?trace=${encodeURIComponent(legacyTrace.id)}`,
      locale,
    });
  }

  const sessionId = sessionBundle.session.id;
  const { targetType, targetId } = sessionBundle.session;

  const [schedules, targets, traces, workspace] = await Promise.all([
    listSchedules(token).catch(() => []),
    resolveTargetSummaries(token, targetType, targetId),
    listTraces(token, { sessionId, limit: 50 }).catch(() => []),
    getWorkspaceInfo(token).catch(() => null),
  ]);

  const [activity] = buildActivityRows({
    sessions: [sessionBundle.session],
    schedules,
    agents: targets.agents,
    teams: targets.teams,
    workflows: targets.workflows,
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

  const timeZone =
    workspace?.timezone || workspace?.tenant_timezone || "UTC";

  return (
    <TracesDetail
      activity={activity}
      messages={sessionBundle.messages}
      traces={traces}
      initialTrace={initialTrace}
      preferredTraceId={selectedTraceId}
      timeZone={timeZone}
    />
  );
}
