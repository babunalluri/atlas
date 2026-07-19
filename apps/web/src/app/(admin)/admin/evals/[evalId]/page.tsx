import { EvalDetail } from "@/components/evals/EvalDetail";
import { getEval } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function EvalDetailPage({
  params,
}: {
  params: Promise<{ evalId: string }>;
}) {
  const { evalId } = await params;
  const definition = await getEval(await getServerAgentOsToken(), evalId);
  return <EvalDetail definition={definition} />;
}
