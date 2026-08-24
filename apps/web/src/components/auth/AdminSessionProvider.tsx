"use client";

import type { Session } from "next-auth";
import { SessionProvider } from "next-auth/react";
import type { ReactNode } from "react";

/**
 * Hydrate a route tree with the same `auth()` session that rendered the page
 * so useSession() is authenticated on first paint (not "Sign in").
 * Sign out must hard-navigate off this tree so this snapshot cannot stick.
 *
 * Nested under the root SessionProvider — disable interval/focus refetch here
 * so desk pages do not double-poll ``/api/auth/session``. Token refresh is
 * driven by ``getAccessToken`` when the access JWT is near expiry.
 */
export function HydratedSessionProvider({
  session,
  children,
}: {
  session: Session | null;
  children: ReactNode;
}) {
  return (
    <SessionProvider
      session={session}
      refetchInterval={0}
      refetchOnWindowFocus={false}
      refetchWhenOffline={false}
    >
      {children}
    </SessionProvider>
  );
}
