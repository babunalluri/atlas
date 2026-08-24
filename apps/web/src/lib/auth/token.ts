"use client";

import { getSession, useSession } from "next-auth/react";
import { useCallback, useRef } from "react";

import {
  effectivePlatformTenantId,
  packAccessContext,
} from "@/lib/auth/access-context";
import { decodeJwtPayload } from "@/lib/auth/keycloak-password";

function devAuthEnabled(): boolean {
  return process.env.NEXT_PUBLIC_DEV_AUTH === "true";
}

type AtlasSession = {
  accessToken?: string;
  error?: string;
};

/** Refresh slightly before Keycloak access-token expiry. */
const TOKEN_REFRESH_SKEW_MS = 60_000;
/** In-memory cache for non-hook callers (streams, one-shot fetches). */
const TOKEN_CACHE_TTL_MS = 30_000;

/**
 * Cache the raw JWT only — never a packed ``token::atlas-platform-tenant=…``
 * string. Soft navigations (``router.replace``) clear the platform-tenant
 * cookie without remounting this module; re-packing on every call keeps the
 * request tenant current without another ``getSession()``.
 */
type CachedToken = {
  rawAccessToken: string;
  freshUntilMs: number;
};

let memoryCache: CachedToken | null = null;

function accessTokenExpiresAtMs(accessToken: string): number | null {
  try {
    const claims = decodeJwtPayload(accessToken);
    const exp = claims.exp;
    if (typeof exp === "number" && Number.isFinite(exp)) {
      return exp * 1000;
    }
  } catch {
    // non-JWT / opaque — treat as unknown lifetime
  }
  return null;
}

function packFromAccessToken(accessToken: string): string {
  return packAccessContext(
    accessToken,
    effectivePlatformTenantId(accessToken),
  );
}

function cacheIsFresh(entry: CachedToken | null, now = Date.now()): boolean {
  if (!entry) return false;
  if (entry.freshUntilMs <= now) return false;
  const exp = accessTokenExpiresAtMs(entry.rawAccessToken);
  if (exp != null && exp - now <= TOKEN_REFRESH_SKEW_MS) return false;
  return true;
}

function remember(accessToken: string): string {
  const exp = accessTokenExpiresAtMs(accessToken);
  const freshUntilMs =
    exp != null
      ? Math.min(exp - TOKEN_REFRESH_SKEW_MS, Date.now() + TOKEN_CACHE_TTL_MS)
      : Date.now() + TOKEN_CACHE_TTL_MS;
  memoryCache = { rawAccessToken: accessToken, freshUntilMs };
  return packFromAccessToken(accessToken);
}

function sessionUsable(session: AtlasSession | null | undefined): string | null {
  if (!session || session.error === "RefreshAccessTokenError") return null;
  const token = session.accessToken;
  if (!token) return null;
  const exp = accessTokenExpiresAtMs(token);
  if (exp != null && exp - Date.now() <= TOKEN_REFRESH_SKEW_MS) return null;
  return token;
}

/**
 * Resolve a bearer token for AgentOS / desk API calls.
 *
 * Uses a short in-memory cache so Signal + Options Lab streams do not hit
 * ``/api/auth/session`` on every reconnect or config fetch. Forces a session
 * round-trip only when the cache is cold or the access token is near expiry
 * (so Auth.js JWT callbacks can still refresh). Tenant packing always uses
 * the current cookie — never a stale packed string from a prior workspace.
 */
export async function getAccessToken(): Promise<string | null> {
  if (typeof window === "undefined") return null;
  if (cacheIsFresh(memoryCache)) {
    return packFromAccessToken(memoryCache!.rawAccessToken);
  }
  try {
    const session = (await getSession()) as AtlasSession | null;
    if (session?.error === "RefreshAccessTokenError") {
      memoryCache = null;
      return null;
    }
    const token = session?.accessToken;
    if (token) {
      return remember(token);
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
  const { data, status } = useSession();
  const bypass = process.env.NEXT_PUBLIC_DEV_AUTH === "true";
  const dataRef = useRef(data);
  dataRef.current = data;

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

    if (cacheIsFresh(memoryCache)) {
      return packFromAccessToken(memoryCache!.rawAccessToken);
    }

    // Prefer SessionProvider state — no network — when the access token is fresh.
    const fromHook = sessionUsable(dataRef.current as AtlasSession | null);
    if (fromHook) {
      return remember(fromHook);
    }

    // Near expiry / missing — hit /api/auth/session so JWT refresh can run.
    const session = (await getSession()) as AtlasSession | null;
    if (session?.error === "RefreshAccessTokenError") {
      memoryCache = null;
      throw new Error("Session expired — sign in again");
    }
    const accessToken = session?.accessToken;
    if (!accessToken) {
      throw new Error("Sign in required");
    }
    return remember(accessToken);
  }, [bypass, status]);

  return {
    getAccessToken: getAccessTokenCb,
    isLoaded: bypass ? true : status !== "loading",
    // Optimistic: real token is resolved when getAccessToken runs (after refresh).
    isSignedIn: bypass ? true : status === "authenticated",
  };
}

/** Test helper — clear the module cache between cases. */
export function resetAccessTokenCacheForTests(): void {
  memoryCache = null;
}
