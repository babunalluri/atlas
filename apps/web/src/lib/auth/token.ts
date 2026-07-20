"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback } from "react";

import {
  browserSelectedTenantId,
  packAccessContext,
} from "@/lib/auth/access-context";

function clerkConfigured(): boolean {
  const key = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
  return !!key && !key.includes("replace_me");
}

function devAuthEnabled(): boolean {
  return (
    process.env.NEXT_PUBLIC_DEV_AUTH === "true" ||
    !process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY ||
    process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY.includes("replace_me")
  );
}

/**
 * Resolve a bearer token for AgentOS calls.
 * Prefers Clerk session JWT; falls back to a local sentinel when
 * NEXT_PUBLIC_DEV_AUTH=true or Clerk is not configured.
 */
export async function getAccessToken(): Promise<string | null> {
  try {
    const clerk = (
      window as unknown as {
        Clerk?: {
          session?: {
            getToken: (opts?: {
              template?: string;
            }) => Promise<string | null>;
          };
        };
      }
    ).Clerk;
    if (clerk?.session?.getToken) {
      const token = await clerk.session.getToken({ template: "agentos" });
      if (token) return packAccessContext(token, browserSelectedTenantId());
      const fallback = await clerk.session.getToken();
      return fallback
        ? packAccessContext(fallback, browserSelectedTenantId())
        : null;
    }
  } catch {
    // Fall through to dev mode.
  }
  if (devAuthEnabled()) {
    return packAccessContext("dev-token", browserSelectedTenantId());
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

/**
 * Hook that resolves a fresh Clerk bearer token for AgentOS.
 * Requires ClerkProvider when Clerk is configured (root layout).
 */
export function useAgentOsToken() {
  const { isLoaded, isSignedIn, getToken } = useAuth();
  const bypass = !clerkConfigured() || process.env.NEXT_PUBLIC_DEV_AUTH === "true";

  const getAccessTokenCb = useCallback(async () => {
    if (bypass) {
      return packAccessContext("dev-token", browserSelectedTenantId());
    }
    if (!isLoaded) {
      throw new Error("Sign in required");
    }
    if (!isSignedIn) {
      throw new Error("Sign in required");
    }
    const token =
      (await getToken({ template: "agentos" })) || (await getToken());
    if (!token) {
      throw new Error("Sign in required");
    }
    return packAccessContext(token, browserSelectedTenantId());
  }, [bypass, getToken, isLoaded, isSignedIn]);

  return {
    getAccessToken: getAccessTokenCb,
    isLoaded: bypass ? true : isLoaded,
    isSignedIn: bypass ? true : Boolean(isSignedIn),
  };
}
