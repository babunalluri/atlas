import { notFound } from "next/navigation";

import { auth } from "@/auth";
import { StockBrokerCustomerDesk } from "@/components/domains/StockBrokerCustomerDesk";
import { WorkflowChooser } from "@/components/chat/WorkflowChooser";
import { getPublicTenantBranding } from "@/lib/api/admin";

export default async function WorkflowChatHome({
  params,
}: {
  params: Promise<{ tenantSlug: string }>;
}) {
  const { tenantSlug } = await params;
  const session = await auth();
  try {
    const tenant = await getPublicTenantBranding(tenantSlug);
    if (tenant.domain === "stock_broker") {
      return (
        <StockBrokerCustomerDesk tenant={tenant} serverSession={session} />
      );
    }
    return <WorkflowChooser tenant={tenant} serverSession={session} />;
  } catch {
    notFound();
  }
}
