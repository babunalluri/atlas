import { EvalList } from "@/components/evals/EvalList";
import { listEvals, listEvalTargets } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function EvalsPage() {
  const token = await getServerAgentOsToken();
  const [evals, targets] = await Promise.all([
    listEvals(token),
    listEvalTargets(token),
  ]);
  return <EvalList evals={evals} targets={targets} />;
}
