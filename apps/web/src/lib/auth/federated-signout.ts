"use client";

import { getSession, signOut } from "next-auth/react";

type FederatedSession = {
  idToken?: string;
  endSessionUrl?: string;
};

/**
 * Clear the Auth.js cookie and end the Keycloak SSO session (RP-initiated logout).
 */
export async function signOutFederated(callbackUrl = "/"): Promise<void> {
  const session = (await getSession()) as FederatedSession | null;
  const idToken = session?.idToken;
  const endSessionUrl = session?.endSessionUrl;

  await signOut({ redirect: false });

  if (typeof window === "undefined") return;

  const absoluteCallback = new URL(callbackUrl, window.location.origin).toString();
  if (idToken && endSessionUrl) {
    const logout = new URL(endSessionUrl);
    logout.searchParams.set("id_token_hint", idToken);
    logout.searchParams.set("post_logout_redirect_uri", absoluteCallback);
    window.location.assign(logout.toString());
    return;
  }
  window.location.assign(absoluteCallback);
}
