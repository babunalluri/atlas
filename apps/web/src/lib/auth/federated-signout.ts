"use client";

import { getSession, signOut } from "next-auth/react";

type FederatedSession = {
  idToken?: string;
  endSessionUrl?: string;
};

/**
 * Clear the Auth.js cookie and end the Keycloak SSO session (RP-initiated logout).
 *
 * Always land on a public page (default `/`). Redirecting back to a
 * protected route (e.g. chat) feels like "auto sign-in" when Keycloak SSO is
 * still active or the user immediately continues.
 */
export async function signOutFederated(callbackUrl = "/"): Promise<void> {
  const session = (await getSession()) as FederatedSession | null;
  const idToken = session?.idToken;
  const endSessionUrl = session?.endSessionUrl;

  await signOut({ redirect: false });

  if (typeof window === "undefined") return;

  const absoluteCallback = new URL(callbackUrl, window.location.origin).toString();
  const logoutBase =
    endSessionUrl ||
    (process.env.NEXT_PUBLIC_AUTH_KEYCLOAK_ISSUER
      ? `${process.env.NEXT_PUBLIC_AUTH_KEYCLOAK_ISSUER.replace(/\/$/, "")}/protocol/openid-connect/logout`
      : null);

  if (logoutBase) {
    const logout = new URL(logoutBase);
    logout.searchParams.set("post_logout_redirect_uri", absoluteCallback);
    if (idToken) {
      logout.searchParams.set("id_token_hint", idToken);
    } else {
      // Keycloak 18+ accepts client_id when id_token_hint is unavailable.
      const clientId = process.env.NEXT_PUBLIC_AUTH_KEYCLOAK_ID || "atlas-web";
      logout.searchParams.set("client_id", clientId);
    }
    window.location.assign(logout.toString());
    return;
  }
  window.location.assign(absoluteCallback);
}
