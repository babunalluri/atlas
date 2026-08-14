import { describe, expect, it } from "vitest";

import {
  applyOAuthAccount,
  attachSessionFromToken,
  fillClaimsFromAccessToken,
  persistAuthorizedUser,
  sessionLooksSignedIn,
  visibleAuthSession,
  type AtlasClientSession,
} from "@/lib/auth/auth-session";

function jwtWithPayload(payload: Record<string, unknown>): string {
  const json = Buffer.from(JSON.stringify(payload)).toString("base64url");
  return `header.${json}.sig`;
}

describe("persistAuthorizedUser", () => {
  it("copies credentials identity and tokens onto the JWT", () => {
    const access = jwtWithPayload({
      sub: "user-1",
      email: "ops@acme.atlas.local",
      name: "Acme Ops",
    });
    const token = persistAuthorizedUser(
      {},
      {
        id: "user-1",
        name: "Acme Ops",
        email: "ops@acme.atlas.local",
        accessToken: access,
        refreshToken: "refresh",
        idToken: "id",
        expiresAt: 1_700_000_000,
        orgId: "org_demo_acme",
        orgRole: "org:admin",
      },
    );
    expect(token.sub).toBe("user-1");
    expect(token.name).toBe("Acme Ops");
    expect(token.email).toBe("ops@acme.atlas.local");
    expect(token.accessToken).toBe(access);
    expect(token.orgId).toBe("org_demo_acme");
  });

  it("does not concatenate identical given and family names", () => {
    const access = jwtWithPayload({
      sub: "user-3",
      email: "babu@atlas.ai",
      given_name: "Babu",
      family_name: "Babu",
    });
    const token = persistAuthorizedUser({}, { id: "user-3", accessToken: access });
    expect(token.name).toBe("Babu");
  });

  it("fills missing email/name from the access token", () => {
    const access = jwtWithPayload({
      sub: "user-2",
      email: "admin@atlas.local",
      preferred_username: "admin",
    });
    const token = persistAuthorizedUser({}, { id: "user-2", accessToken: access });
    expect(token.email).toBe("admin@atlas.local");
    expect(token.name).toBe("admin");
    expect(token.sub).toBe("user-2");
  });
});

describe("applyOAuthAccount", () => {
  it("stores Keycloak OIDC tokens and claims", () => {
    const access = jwtWithPayload({
      sub: "kc-1",
      email: "admin@atlas.local",
      name: "Atlas Admin",
      org_id: "org_demo_acme",
    });
    const token = applyOAuthAccount(
      {},
      {
        access_token: access,
        refresh_token: "refresh",
        id_token: "id",
        expires_at: 1_700_000_000,
      },
    );
    expect(token.accessToken).toBe(access);
    expect(token.email).toBe("admin@atlas.local");
    expect(token.orgId).toBe("org_demo_acme");
  });
});

describe("attachSessionFromToken", () => {
  it("maps JWT claims onto session.user so useSession is authenticated", () => {
    const seed: AtlasClientSession = {
      user: { name: undefined, email: undefined, image: undefined },
    };
    const session = attachSessionFromToken(
      seed,
      {
        sub: "user-1",
        name: "Acme Ops",
        email: "ops@acme.atlas.local",
        accessToken: "access",
        orgId: "org_demo_acme",
        orgRole: "org:member",
      },
      "http://localhost:8080/realms/atlas/protocol/openid-connect/logout",
    );
    expect(session.user?.id).toBe("user-1");
    expect(session.user?.email).toBe("ops@acme.atlas.local");
    expect(session.user?.name).toBe("Acme Ops");
    expect(session.accessToken).toBe("access");
    expect(session.orgRole).toBe("org:member");
    expect(sessionLooksSignedIn(session)).toBe(true);
  });
});

describe("sessionLooksSignedIn", () => {
  it("is false for an empty client session", () => {
    expect(sessionLooksSignedIn(null)).toBe(false);
    expect(sessionLooksSignedIn({})).toBe(false);
  });

  it("is true when only an access token is present", () => {
    expect(sessionLooksSignedIn({ accessToken: "access" })).toBe(true);
  });
});

describe("visibleAuthSession", () => {
  const hydrated = {
    accessToken: "server-access",
    user: { email: "ops@acme.atlas.local" },
  };

  it("uses the server session only while Auth.js is still loading", () => {
    expect(visibleAuthSession("loading", null, hydrated)).toEqual(hydrated);
  });

  it("does not resurrect a hydrated session after Sign out", () => {
    expect(visibleAuthSession("unauthenticated", null, hydrated)).toBeNull();
  });

  it("prefers the live client session once authenticated", () => {
    const live = { accessToken: "fresh", user: { email: "ops@acme.atlas.local" } };
    expect(visibleAuthSession("authenticated", live, hydrated)).toEqual(live);
  });
});

describe("fillClaimsFromAccessToken", () => {
  it("does not overwrite identity already on the token", () => {
    const access = jwtWithPayload({
      email: "other@atlas.local",
      name: "Other",
    });
    const token = {
      email: "ops@acme.atlas.local",
      name: "Acme Ops",
      accessToken: access,
    };
    fillClaimsFromAccessToken(token);
    expect(token.email).toBe("ops@acme.atlas.local");
    expect(token.name).toBe("Acme Ops");
  });

  it("upgrades org:member to org:admin when the JWT lists both", () => {
    const access = jwtWithPayload({
      org_role: ["org:member", "org:admin"],
    });
    const token = {
      orgRole: "org:member",
      accessToken: access,
    };
    fillClaimsFromAccessToken(token);
    expect(token.orgRole).toBe("org:admin");
  });
});
