import "server-only";

import { auth } from "@clerk/nextjs/server";
import { cookies } from "next/headers";

import {
  packAccessContext,
  PLATFORM_TENANT_COOKIE,
} from "@/lib/auth/access-context";

export async function getServerAgentOsToken(): Promise<string> {
  const platformTenantId = (await cookies()).get(PLATFORM_TENANT_COOKIE)?.value;
  if (process.env.NEXT_PUBLIC_DEV_AUTH === "true") {
    return packAccessContext("development", platformTenantId);
  }
  const session = await auth();
  const token = await session.getToken({ template: "agentos" });
  if (!token) {
    throw new Error("Authentication is required");
  }
  return packAccessContext(token, platformTenantId);
}
