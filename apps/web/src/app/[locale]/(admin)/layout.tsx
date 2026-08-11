import { Suspense } from "react";

import { AdminPageSkeleton } from "@/components/layout/AdminPageSkeleton";
import { AdminShell } from "@/components/layout/AdminShell";

// Admin pages read Clerk cookies / tokens server-side; keep the segment dynamic.
export const dynamic = "force-dynamic";

export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AdminShell>
      <Suspense fallback={<AdminPageSkeleton />}>{children}</Suspense>
    </AdminShell>
  );
}
