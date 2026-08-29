import { redirect } from "@/i18n/navigation";

import { getWorkspaceInfo } from "@/lib/api/admin";
import { getServerAgentOsToken } from "@/lib/auth/server-token";

export const dynamic = "force-dynamic";

/** Legacy route — admin trading desk lives on /t/{slug}/chat. */
export default async function AdminDeskRedirectPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  const workspace = await getWorkspaceInfo(await getServerAgentOsToken());
  redirect({
    // Straight to the landing: /desk is itself only a redirect to /workspace.
    href: `/t/${workspace.slug}/workspace`,
    locale,
  });
}
