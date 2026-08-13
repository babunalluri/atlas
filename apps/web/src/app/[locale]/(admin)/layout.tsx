import { Suspense } from "react";
import { redirect } from "next/navigation";

import { auth } from "@/auth";
import { AdminSessionProvider } from "@/components/auth/AdminSessionProvider";
import { AdminPageSkeleton } from "@/components/layout/AdminPageSkeleton";
import { AdminShell } from "@/components/layout/AdminShell";

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

  return (
    <AdminSessionProvider session={session}>
      <AdminShell serverSession={session}>
        <Suspense fallback={<AdminPageSkeleton />}>{children}</Suspense>
      </AdminShell>
    </AdminSessionProvider>
  );
}
