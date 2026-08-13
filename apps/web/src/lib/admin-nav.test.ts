import { describe, expect, it } from "vitest";

import {
  filterAdminNavItems,
  isAdminNavItemVisible,
  PLATFORM_ADMIN_ONLY_NAV_KEYS,
} from "@/lib/admin-nav";

const TENANT_ADMIN_NAV_KEYS = [
  "workflows",
  "teams",
  "agents",
  "tools",
  "traces",
  "approvals",
  "schedules",
  "metrics",
  "knowledge",
  "users",
  "notifications",
  "credentials",
] as const;

const ALL_NAV_KEYS = [
  ...TENANT_ADMIN_NAV_KEYS,
  ...PLATFORM_ADMIN_ONLY_NAV_KEYS,
];

describe("isAdminNavItemVisible", () => {
  it("shows workspace ops items to tenant admins and hides platform-only keys", () => {
    const visible = ALL_NAV_KEYS.filter((key) =>
      isAdminNavItemVisible(key, false),
    );
    expect(visible).toEqual([...TENANT_ADMIN_NAV_KEYS]);
  });

  it("shows every sidebar item to Super Admin", () => {
    expect(
      ALL_NAV_KEYS.every((key) => isAdminNavItemVisible(key, true)),
    ).toBe(true);
  });
});

describe("filterAdminNavItems", () => {
  it("drops platform-only entries for tenant admins", () => {
    const items = ALL_NAV_KEYS.map((navKey) => ({ navKey }));
    expect(filterAdminNavItems(items, false).map((item) => item.navKey)).toEqual(
      [...TENANT_ADMIN_NAV_KEYS],
    );
    expect(filterAdminNavItems(items, true)).toHaveLength(ALL_NAV_KEYS.length);
  });
});
