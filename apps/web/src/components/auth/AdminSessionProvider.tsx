"use client";

import type { Session } from "next-auth";
import { SessionProvider } from "next-auth/react";
import type { ReactNode } from "react";

/**
 * Hydrate the admin tree with the same `auth()` session that rendered the page
 * so useSession() is authenticated on first paint (not "Sign in").
 */
export function AdminSessionProvider({
  session,
  children,
}: {
  session: Session | null;
  children: ReactNode;
}) {
  return (
    <SessionProvider session={session} refetchInterval={4 * 60} refetchOnWindowFocus>
      {children}
    </SessionProvider>
  );
}
