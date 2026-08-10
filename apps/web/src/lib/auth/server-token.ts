import "server-only";

import { auth } from "@clerk/nextjs/server";
import { cookies } from "next/headers";

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
  const token = await session.getToken({ template: "agentos" });
  if (!token) {
    throw new Error("Authentication is required");
  }

  // Stale Platform → Open workspace cookies must not ride along for
  // non–platform-admin JWTs (e.g. after switching to a normal org).
  if (selectedTenantId && !tokenIsPlatformAdmin(token)) {
    return packAccessContext(token, null);
  }

  return packAccessContext(
    token,
    effectivePlatformTenantId(token, selectedTenantId),
  );
}
