import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next-auth/react", () => ({
  getSession: vi.fn(),
  useSession: vi.fn(() => ({
    data: null,
    status: "unauthenticated",
    update: vi.fn(),
  })),
}));

import { getSession } from "next-auth/react";

import { PLATFORM_TENANT_COOKIE } from "@/lib/auth/access-context";
import {
  getAccessToken,
  resetAccessTokenCacheForTests,
} from "@/lib/auth/token";

function jwtWithClaims(claims: Record<string, unknown>): string {
  const header = Buffer.from(JSON.stringify({ alg: "none" })).toString(
    "base64url",
  );
  const payload = Buffer.from(JSON.stringify(claims)).toString("base64url");
  return `${header}.${payload}.sig`;
}

function jwtWithExp(expSeconds: number): string {
  return jwtWithClaims({ sub: "u1", exp: expSeconds });
}

function platformAdminJwt(expSeconds: number): string {
  return jwtWithClaims({
    sub: "u1",
    exp: expSeconds,
    platform_admin: true,
  });
}

describe("getAccessToken caching", () => {
  beforeEach(() => {
    resetAccessTokenCacheForTests();
    vi.mocked(getSession).mockReset();
    process.env.NEXT_PUBLIC_DEV_AUTH = "false";
    // token.ts guards on window (SSR); vitest defaults to node.
    vi.stubGlobal("window", {} as Window & typeof globalThis);
    vi.stubGlobal("document", {
      cookie: "",
    } as Document);
  });

  it("reuses the in-memory cache instead of calling getSession every time", async () => {
    const token = jwtWithExp(Math.floor(Date.now() / 1000) + 3600);
    vi.mocked(getSession).mockResolvedValue({ accessToken: token } as never);

    const first = await getAccessToken();
    const second = await getAccessToken();

    expect(first).toBeTruthy();
    expect(second).toBe(first);
    expect(getSession).toHaveBeenCalledTimes(1);
  });

  it("does not treat a near-expiry token as a long-lived cache hit", async () => {
    const nearExp = jwtWithExp(Math.floor(Date.now() / 1000) + 30);
    const fresh = jwtWithExp(Math.floor(Date.now() / 1000) + 3600);
    vi.mocked(getSession)
      .mockResolvedValueOnce({ accessToken: nearExp } as never)
      .mockResolvedValueOnce({ accessToken: fresh } as never);

    await getAccessToken();
    // Near-exp token must not satisfy cacheIsFresh — second call hits network.
    await getAccessToken();
    expect(getSession).toHaveBeenCalledTimes(2);
  });

  it("re-packs with the current platform tenant on a cache hit", async () => {
    const token = platformAdminJwt(Math.floor(Date.now() / 1000) + 3600);
    vi.mocked(getSession).mockResolvedValue({ accessToken: token } as never);

    (
      document as { cookie: string }
    ).cookie = `${PLATFORM_TENANT_COOKIE}=tenant-a`;
    const first = await getAccessToken();
    expect(first).toContain("::atlas-platform-tenant=tenant-a");

    // Soft leave-workspace: cookie cleared, module cache still warm.
    (document as { cookie: string }).cookie = "";
    const second = await getAccessToken();

    expect(getSession).toHaveBeenCalledTimes(1);
    expect(second).toBe(token);
    expect(second).not.toContain("::atlas-platform-tenant=");
  });
});
