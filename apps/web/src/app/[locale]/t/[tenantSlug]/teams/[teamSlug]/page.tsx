import { auth } from "@/auth";
import { CustomerChat } from "@/components/chat/CustomerChat";
import { getPublicTeamChatSurface } from "@/lib/api/admin";
import { notFound } from "next/navigation";

export default async function CustomerTeamChatPage({
  params,
}: {
  params: Promise<{ tenantSlug: string; teamSlug: string }>;
}) {
  const { tenantSlug, teamSlug } = await params;
  const session = await auth();
  let surface;
  try {
    surface = await getPublicTeamChatSurface(tenantSlug, teamSlug);
  } catch {
    notFound();
  }
  return <CustomerChat surface={surface} serverSession={session} />;
}
