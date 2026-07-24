import { notFound } from "next/navigation";

import { ServiceAccountEditor } from "@/components/security/ServiceAccountEditor";
import { getServiceAccount } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

export default async function ServiceAccountDetailPage({
  params,
}: {
  params: Promise<{ accountId: string }>;
}) {
  const { accountId } = await params;
  try {
    const account = await getServiceAccount(
      await getServerAgentOsToken(),
      accountId,
    );
    return <ServiceAccountEditor mode="edit" initial={account} />;
  } catch {
    notFound();
  }
}
