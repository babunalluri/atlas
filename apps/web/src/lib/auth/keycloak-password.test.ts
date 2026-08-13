import { afterEach, describe, expect, it, vi } from "vitest";

import {
  decodeJwtPayload,
  exchangePasswordGrant,
  firstStringClaim,
  PasswordGrantAuthError,
  sessionFromPasswordGrant,
} from "@/lib/auth/keycloak-password";

function jwtWithPayload(payload: Record<string, unknown>): string {
  const json = Buffer.from(JSON.stringify(payload)).toString("base64url");
  return `header.${json}.sig`;
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("firstStringClaim", () => {
  it("reads a string or the first array value", () => {
    expect(firstStringClaim("org_demo_acme")).toBe("org_demo_acme");
    expect(firstStringClaim(["org_demo_acme", "other"])).toBe("org_demo_acme");
    expect(firstStringClaim("")).toBeUndefined();
    expect(firstStringClaim([])).toBeUndefined();
  });
});

describe("sessionFromPasswordGrant", () => {
  it("maps Keycloak token claims onto the Auth.js session shape", () => {
    const access = jwtWithPayload({
      sub: "user-1",
      email: "ops@acme.atlas.local",
      name: "Acme Ops",
      org_id: ["org_demo_acme"],
      org_role: "org:admin",
    });
    const session = sessionFromPasswordGrant({
      access_token: access,
      refresh_token: "refresh",
      id_token: "id",
      expires_in: 120,
    });
    expect(session.sub).toBe("user-1");
    expect(session.email).toBe("ops@acme.atlas.local");
    expect(session.orgId).toBe("org_demo_acme");
    expect(session.orgRole).toBe("org:admin");
    expect(session.accessToken).toBe(access);
    expect(session.refreshToken).toBe("refresh");
    expect(session.idToken).toBe("id");
  });
});

describe("decodeJwtPayload", () => {
  it("decodes a compact JWT payload", () => {
    const token = jwtWithPayload({ sub: "abc", org_id: "org_demo_acme" });
    expect(decodeJwtPayload(token)).toMatchObject({
      sub: "abc",
      org_id: "org_demo_acme",
    });
  });
});

describe("exchangePasswordGrant", () => {
  it("POSTs a password grant and does not put the password on the result", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_KEYCLOAK_ID", "atlas-web");
    vi.stubEnv("AUTH_KEYCLOAK_SECRET", "atlas-web-dev-secret-change-me");
    vi.stubEnv("AUTH_KEYCLOAK_ISSUER", "http://localhost:8080/realms/atlas");
    const access = jwtWithPayload({
      sub: "user-1",
      email: "admin@atlas.local",
    });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        access_token: access,
        refresh_token: "refresh",
        id_token: "id",
        expires_in: 300,
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const session = await exchangePasswordGrant(
      "admin@atlas.local",
      "super-secret-password",
    );
    expect(session.accessToken).toBe(access);
    expect(JSON.stringify(session)).not.toContain("super-secret-password");

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const body = String(init.body);
    expect(body).toContain("grant_type=password");
    expect(body).toContain("client_id=atlas-web");
    expect(body).toContain("username=admin%40atlas.local");
    expect(fetchMock.mock.calls[0]?.[0]).toContain(
      "/protocol/openid-connect/token",
    );
  });

  it("maps invalid_grant to invalid_credentials without leaking the body", async () => {
    vi.stubEnv("NODE_ENV", "development");
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({
        error: "invalid_grant",
        error_description: "Invalid user credentials",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      exchangePasswordGrant("admin@atlas.local", "wrong-password"),
    ).rejects.toMatchObject({
      name: "PasswordGrantAuthError",
      code: "invalid_credentials",
    } satisfies Partial<PasswordGrantAuthError>);
  });
});
