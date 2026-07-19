import { ScheduleManager } from "@/components/schedules/ScheduleManager";
import { listSchedules, listScheduleTargets } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function SchedulesPage() {
  const token = await getServerAgentOsToken();
  const [schedules, targets] = await Promise.all([
    listSchedules(token),
    listScheduleTargets(token),
  ]);
  return <ScheduleManager schedules={schedules} targets={targets} />;
}
