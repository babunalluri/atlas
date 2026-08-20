import { describe, expect, it } from "vitest";

import { canOpenOrgAdmin, ORG_ADMIN_HREF } from "@/lib/auth/desk-admin";

describe("canOpenOrgAdmin", () => {
  it("shows Admin for tenant admins", () => {
    expect(
      canOpenOrgAdmin({ can_administer: true, role: "tenant_admin" }),
    ).toBe(true);
  });

  it("shows Admin for Super Admin", () => {
    expect(
      canOpenOrgAdmin({ can_administer: true, role: "platform_admin" }),
    ).toBe(true);
  });

  it("hides Admin for end users", () => {
    expect(
      canOpenOrgAdmin({ can_administer: false, role: "end_user" }),
    ).toBe(false);
  });

  it("hides Admin when workspace is missing", () => {
    expect(canOpenOrgAdmin(null)).toBe(false);
  });

  it("uses tenant_admin / platform_admin when can_administer is omitted", () => {
    expect(canOpenOrgAdmin({ role: "tenant_admin" })).toBe(true);
    expect(canOpenOrgAdmin({ role: "platform_admin" })).toBe(true);
    expect(canOpenOrgAdmin({ role: "end_user" })).toBe(false);
  });
});

describe("ORG_ADMIN_HREF", () => {
  it("opens the org admin app home", () => {
    expect(ORG_ADMIN_HREF).toBe("/admin/teams");
  });
});
