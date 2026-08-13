/**
 * Admin left-nav visibility. Pages stay reachable by URL; this only hides
 * sidebar entries. Super Admin (platform_admin) sees the full menu.
 *
 * Tenant/org admins configure this workspace (agents, teams, tools, users,
 * credentials, knowledge). They do not need platform plumbing, Agno memory
 * labs, or day-one M2M surfaces.
 */

export const PLATFORM_ADMIN_ONLY_NAV_KEYS = [
  "superAdmin",
  "sandboxPackages",
  "toolkitCatalog",
  "evaluations",
  "memories",
  "learnings",
  "billing",
  "publicApi",
  "serviceAccounts",
  "mcpServer",
] as const;

export type PlatformAdminOnlyNavKey =
  (typeof PLATFORM_ADMIN_ONLY_NAV_KEYS)[number];

const PLATFORM_ADMIN_ONLY = new Set<string>(PLATFORM_ADMIN_ONLY_NAV_KEYS);

export function isAdminNavItemVisible(
  navKey: string,
  isPlatformAdmin: boolean,
): boolean {
  if (isPlatformAdmin) return true;
  return !PLATFORM_ADMIN_ONLY.has(navKey);
}

export function filterAdminNavItems<T extends { navKey: string }>(
  items: T[],
  isPlatformAdmin: boolean,
): T[] {
  return items.filter((item) =>
    isAdminNavItemVisible(item.navKey, isPlatformAdmin),
  );
}
