import "server-only";

import { cookies } from "next/headers";

import { auth } from "@/auth";
import {
  effectivePlatformTenantId,
  packAccessContext,
  PLATFORM_TENANT_COOKIE,
  tokenIsPlatformAdmin,
} from "@/lib/auth/access-context";

export async function getServerAgentOsToken(): Promise<string> {
  const jar = await cookies();
  const selectedTenantId = jar.get(PLATFORM_TENANT_COOKIE)?.value ?? null;

  if (process.env.NEXT_PUBLIC_DEV_AUTH === "true") {
    return packAccessContext(
      "development",
      effectivePlatformTenantId("development", selectedTenantId),
    );
  }

  const session = await auth();
  const typed = session as {
    accessToken?: string;
    error?: string;
  } | null;
  if (typed?.error === "RefreshAccessTokenError") {
    throw new Error("Session expired — sign in again");
  }
  const token = typed?.accessToken;
  if (!token) {
    throw new Error("Authentication is required");
  }

  if (selectedTenantId && !tokenIsPlatformAdmin(token)) {
    return packAccessContext(token, null);
  }

  return packAccessContext(
    token,
    effectivePlatformTenantId(token, selectedTenantId),
  );
}
