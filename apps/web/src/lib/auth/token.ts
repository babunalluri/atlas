"use client";

import { getSession, useSession } from "next-auth/react";
import { useCallback } from "react";

import {
  effectivePlatformTenantId,
  packAccessContext,
} from "@/lib/auth/access-context";

function devAuthEnabled(): boolean {
  return process.env.NEXT_PUBLIC_DEV_AUTH === "true";
}

type AtlasSession = {
  accessToken?: string;
  error?: string;
};

/**
 * Resolve a bearer token for AgentOS calls (Keycloak access token via Auth.js).
 * Always hits `/api/auth/session` so the JWT callback can refresh expired tokens.
 */
export async function getAccessToken(): Promise<string | null> {
  if (typeof window === "undefined") return null;
  try {
    const session = (await getSession()) as AtlasSession | null;
    if (session?.error === "RefreshAccessTokenError") {
      return null;
    }
    const token = session?.accessToken;
    if (token) {
      return packAccessContext(token, effectivePlatformTenantId(token));
    }
  } catch {
    // fall through
  }
  if (devAuthEnabled()) {
    return packAccessContext(
      "dev-token",
      effectivePlatformTenantId("dev-token"),
    );
  }
  return null;
}

export function devTenantHeaders(tenantId?: string): Record<string, string> {
  if (process.env.NEXT_PUBLIC_DEV_AUTH !== "true") return {};
  return {
    "x-dev-tenant-id":
      tenantId ||
      process.env.NEXT_PUBLIC_DEV_TENANT_ID ||
      "11111111-1111-1111-1111-111111111111",
    "x-dev-user-id": process.env.NEXT_PUBLIC_DEV_USER_ID || "dev-admin",
    "x-dev-role": process.env.NEXT_PUBLIC_DEV_ROLE || "tenant_admin",
  };
}

export function useAgentOsToken() {
  const { status } = useSession();
  const bypass = process.env.NEXT_PUBLIC_DEV_AUTH === "true";

  const getAccessTokenCb = useCallback(async () => {
    if (bypass) {
      return packAccessContext(
        "dev-token",
        effectivePlatformTenantId("dev-token"),
      );
    }
    if (status === "loading") {
      throw new Error("Sign in required");
    }
    const session = (await getSession()) as AtlasSession | null;
    if (session?.error === "RefreshAccessTokenError") {
      throw new Error("Session expired — sign in again");
    }
    const accessToken = session?.accessToken;
    if (!accessToken) {
      throw new Error("Sign in required");
    }
    return packAccessContext(
      accessToken,
      effectivePlatformTenantId(accessToken),
    );
  }, [bypass, status]);

  return {
    getAccessToken: getAccessTokenCb,
    isLoaded: bypass ? true : status !== "loading",
    // Optimistic: real token is resolved when getAccessToken runs (after refresh).
    isSignedIn: bypass ? true : status === "authenticated",
  };
}
