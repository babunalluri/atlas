import { describe, expect, it } from "vitest";

import { ORG_ADMIN_HREF } from "@/lib/auth/desk-admin";
import {
  allowsAdminApp,
  claimsAllowAdmin,
  isGenericAdminLanding,
  isWorkspacePath,
  localePrefixedPath,
  PLATFORM_TENANTS_HREF,
  resolvePostLoginHref,
  stripLocalePrefix,
  workspaceDeskHref,
} from "@/lib/auth/post-login";

function jwtWithPayload(payload: Record<string, unknown>): string {
  const json = Buffer.from(JSON.stringify(payload)).toString("base64url");
  return `header.${json}.sig`;
}

describe("stripLocalePrefix", () => {
  it("drops the locale segment", () => {
    expect(stripLocalePrefix("/en/admin")).toBe("/admin");
    expect(stripLocalePrefix("/pt-BR/t/acme/chat")).toBe("/t/acme/chat");
    expect(stripLocalePrefix("/admin/users")).toBe("/admin/users");
  });
});

describe("isWorkspacePath / isGenericAdminLanding", () => {
  it("treats tenant desks as workspace destinations", () => {
    expect(isWorkspacePath("/t/acme/chat")).toBe(true);
    expect(isWorkspacePath("/en/t/acme/chat")).toBe(true);
    expect(isWorkspacePath("/chat")).toBe(true);
    expect(isWorkspacePath("/en/admin")).toBe(false);
  });

  it("treats default admin index URLs as generic landings", () => {
    expect(isGenericAdminLanding("/en/admin")).toBe(true);
    expect(isGenericAdminLanding("/admin")).toBe(true);
    expect(isGenericAdminLanding("/admin/users")).toBe(false);
  });
});

describe("claimsAllowAdmin", () => {
  it("treats org:member as an end user even when callback would be admin", () => {
    const access = jwtWithPayload({
      sub: "babu",
      org_role: "org:member",
    });
    expect(claimsAllowAdmin({ accessToken: access, orgRole: "org:member" })).toBe(
      false,
    );
  });

  it("treats org:admin as a tenant admin", () => {
    const access = jwtWithPayload({
      sub: "ops",
      org_role: "org:admin",
    });
    expect(claimsAllowAdmin({ accessToken: access, orgRole: "org:admin" })).toBe(
      true,
    );
  });

  it("prefers org:admin when the claim is a mixed list", () => {
    const access = jwtWithPayload({
      sub: "ops",
      org_role: ["org:member", "org:admin"],
    });
    expect(claimsAllowAdmin({ accessToken: access })).toBe(true);
  });

  it("treats platform_admin as admin", () => {
    const access = jwtWithPayload({
      sub: "root",
      org_role: "org:admin",
      platform_admin: true,
    });
    expect(claimsAllowAdmin({ accessToken: access })).toBe(true);
  });

  it("treats tenant_admin session role as admin", () => {
    expect(claimsAllowAdmin({ orgRole: "tenant_admin" })).toBe(true);
    expect(claimsAllowAdmin({ orgRole: "platform_admin" })).toBe(true);
  });
});

describe("allowsAdminApp", () => {
  it("does not redirect tenant_admin when JWT org_role is missing", () => {
    const access = jwtWithPayload({ sub: "owner", org_id: "org_acme" });
    expect(
      allowsAdminApp({
        accessToken: access,
        workspace: { can_administer: true, role: "tenant_admin" },
      }),
    ).toBe(true);
  });

  it("does not redirect platform_admin", () => {
    const access = jwtWithPayload({
      sub: "root",
      org_role: "org:admin",
      platform_admin: true,
    });
    expect(allowsAdminApp({ accessToken: access })).toBe(true);
  });

  it("redirects end users away from admin even with a workspace slug", () => {
    const access = jwtWithPayload({
      sub: "babu",
      org_role: "org:member",
    });
    expect(
      allowsAdminApp({
        accessToken: access,
        orgRole: "org:member",
        workspace: { can_administer: false, role: "end_user" },
      }),
    ).toBe(false);
  });
});

describe("resolvePostLoginHref", () => {
  const memberToken = jwtWithPayload({
    sub: "babu",
    org_role: "org:member",
  });
  const adminToken = jwtWithPayload({
    sub: "ops",
    org_role: "org:admin",
  });
  const platformToken = jwtWithPayload({
    sub: "root",
    org_role: "org:admin",
    platform_admin: true,
  });

  it("sends end users to the tenant desk, ignoring /admin callback", async () => {
    await expect(
      resolvePostLoginHref({
        accessToken: memberToken,
        orgRole: "org:member",
        callbackUrl: "/en/admin",
        loadWorkspace: async () => ({
          slug: "stock-broker",
          can_administer: false,
        }),
      }),
    ).resolves.toBe("/t/stock-broker/chat");
  });

  it("keeps an explicit workspace callback for end users", async () => {
    await expect(
      resolvePostLoginHref({
        accessToken: memberToken,
        orgRole: "org:member",
        callbackUrl: "/t/acme/chat",
      }),
    ).resolves.toBe("/t/acme/chat");
  });

  it("falls back to /chat when the workspace slug is unknown", async () => {
    await expect(
      resolvePostLoginHref({
        accessToken: memberToken,
        orgRole: "org:member",
        callbackUrl: "/admin",
      }),
    ).resolves.toBe("/chat");
  });

  it("never honors an admin deep link for end users", async () => {
    await expect(
      resolvePostLoginHref({
        accessToken: memberToken,
        orgRole: "org:member",
        callbackUrl: "/admin/users",
        loadWorkspace: async () => ({ slug: "acme", can_administer: false }),
      }),
    ).resolves.toBe("/t/acme/chat");
  });

  it("sends tenant admins to org admin home from a generic /admin callback", async () => {
    await expect(
      resolvePostLoginHref({
        accessToken: adminToken,
        orgRole: "org:admin",
        callbackUrl: "/en/admin",
      }),
    ).resolves.toBe(ORG_ADMIN_HREF);
  });

  it("keeps a specific admin deep link for tenant admins", async () => {
    await expect(
      resolvePostLoginHref({
        accessToken: adminToken,
        orgRole: "org:admin",
        callbackUrl: "/admin/users",
      }),
    ).resolves.toBe("/admin/users");
  });

  it("sends Super Admin to the platform tenants list from a generic landing", async () => {
    await expect(
      resolvePostLoginHref({
        accessToken: platformToken,
        orgRole: "org:admin",
        callbackUrl: "/en/admin",
      }),
    ).resolves.toBe(PLATFORM_TENANTS_HREF);
  });
});

describe("workspaceDeskHref / localePrefixedPath", () => {
  it("builds a tenant chat path", () => {
    expect(workspaceDeskHref("acme")).toBe("/t/acme/chat");
  });

  it("prefixes locale without doubling it", () => {
    expect(localePrefixedPath("en", "/t/acme/chat")).toBe("/en/t/acme/chat");
    expect(localePrefixedPath("en", "/en/admin")).toBe("/en/admin");
    expect(localePrefixedPath("en", ORG_ADMIN_HREF)).toBe("/en/admin/teams");
  });
});
