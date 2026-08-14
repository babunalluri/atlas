import { Suspense } from "react";
import { redirect } from "next/navigation";

import { auth } from "@/auth";
import { HydratedSessionProvider } from "@/components/auth/AdminSessionProvider";
import { AdminPageSkeleton } from "@/components/layout/AdminPageSkeleton";
import { AdminShell } from "@/components/layout/AdminShell";
import { getWorkspaceInfo } from "@/lib/api/admin";
import {
  allowsAdminApp,
  claimsAllowAdmin,
  localePrefixedPath,
  workspaceDeskHref,
} from "@/lib/auth/post-login";

// Admin pages read session cookies / tokens server-side; keep the segment dynamic.
export const dynamic = "force-dynamic";

export default async function AdminLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  const session = await auth();
  const allowDevBypass = process.env.NEXT_PUBLIC_DEV_AUTH === "true";

  if (!session && !allowDevBypass) {
    redirect(`/${locale}?signin=1`);
  }

  const typed = session as {
    accessToken?: string;
    orgRole?: string;
  } | null;
  if (
    typed?.accessToken &&
    !allowDevBypass &&
    !claimsAllowAdmin({
      accessToken: typed.accessToken,
      orgRole: typed.orgRole,
    })
  ) {
    let workspace: Awaited<ReturnType<typeof getWorkspaceInfo>> | null = null;
    try {
      workspace = await getWorkspaceInfo(typed.accessToken);
    } catch {
      workspace = null;
    }
    if (
      !allowsAdminApp({
        accessToken: typed.accessToken,
        orgRole: typed.orgRole,
        workspace,
      })
    ) {
      const dest = workspace?.slug
        ? localePrefixedPath(locale, workspaceDeskHref(workspace.slug))
        : localePrefixedPath(locale, "/chat");
      redirect(dest);
    }
  }

  return (
    <HydratedSessionProvider session={session}>
      <AdminShell serverSession={session}>
        <Suspense fallback={<AdminPageSkeleton />}>{children}</Suspense>
      </AdminShell>
    </HydratedSessionProvider>
  );
}
