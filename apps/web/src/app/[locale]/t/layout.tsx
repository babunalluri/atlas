import { auth } from "@/auth";

import { HydratedSessionProvider } from "@/components/auth/AdminSessionProvider";

// Customer desks and hosted chat read the session so the header can show who is signed in.
export const dynamic = "force-dynamic";

export default async function TenantLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await auth();
  return (
    <HydratedSessionProvider session={session}>{children}</HydratedSessionProvider>
  );
}
