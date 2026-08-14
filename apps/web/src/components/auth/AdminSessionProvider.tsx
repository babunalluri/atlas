"use client";

import type { Session } from "next-auth";
import { SessionProvider } from "next-auth/react";
import type { ReactNode } from "react";

/**
 * Hydrate a route tree with the same `auth()` session that rendered the page
 * so useSession() is authenticated on first paint (not "Sign in").
 * Sign out must hard-navigate off this tree so this snapshot cannot stick.
 */
export function HydratedSessionProvider({
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
