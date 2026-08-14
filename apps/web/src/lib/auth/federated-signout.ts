"use client";

import { signOut } from "next-auth/react";

import {
  atlasSignedOutHomePath,
  expireAtlasAuthCookies,
} from "@/lib/auth/sign-out";

/**
 * End the Atlas (Auth.js) session and land on the signed-out locale home.
 *
 * Password-grant sign-in never creates a browser IdP SSO session, so this
 * does not redirect through an IdP logout URL (that bounce left the Auth.js
 * cookie in place and HomeHero sent the user back to the desk).
 */
export async function signOutFederated(callbackUrl = "/"): Promise<void> {
  const home = atlasSignedOutHomePath(callbackUrl);
  try {
    await signOut({ redirect: false, callbackUrl: home });
  } catch {
    // Still leave the authenticated shell; cookies are expired below.
  }
  expireAtlasAuthCookies();
  if (typeof window !== "undefined") {
    window.location.replace(home);
  }
}

export { atlasSignedOutHomePath };
