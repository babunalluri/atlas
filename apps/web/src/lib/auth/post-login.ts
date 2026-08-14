import { routing } from "@/i18n/routing";
import { tokenIsPlatformAdmin } from "@/lib/auth/access-context";
import { safeAuthCallbackUrl } from "@/lib/auth/callback-url";
import { canOpenOrgAdmin, ORG_ADMIN_HREF } from "@/lib/auth/desk-admin";
import {
  decodeJwtPayload,
  firstStringClaim,
} from "@/lib/auth/keycloak-password";

/** Super Admin home — tenant catalog, not a tenant control plane. */
export const PLATFORM_TENANTS_HREF = "/admin/platform/tenants";

const ADMIN_ORG_ROLES = new Set([
  "org:admin",
  "admin",
  "org:owner",
  "tenant_admin",
  "platform_admin",
]);

const ORG_ROLE_RANK: Record<string, number> = {
  "org:owner": 40,
  "org:admin": 30,
  admin: 30,
  tenant_admin: 30,
  platform_admin: 50,
  "org:member": 10,
  member: 10,
  end_user: 10,
};

export type PostLoginWorkspace = {
  slug?: string | null;
  can_administer?: boolean;
};

/**
 * Pick the most privileged org_role when the access token lists several.
 */
export function bestOrgRole(value: unknown): string | undefined {
  const candidates: string[] = [];
  if (typeof value === "string" && value.trim()) {
    candidates.push(value.trim());
  } else if (Array.isArray(value)) {
    for (const item of value) {
      if (typeof item === "string" && item.trim()) {
        candidates.push(item.trim());
      }
    }
  }
  if (candidates.length === 0) return undefined;
  return candidates.reduce((best, current) =>
    (ORG_ROLE_RANK[current] ?? 0) > (ORG_ROLE_RANK[best] ?? 0) ? current : best,
  );
}

export function stripLocalePrefix(pathname: string): string {
  const pathOnly = pathname.split(/[?#]/)[0] || "/";
  const parts = pathOnly.split("/");
  const maybeLocale = parts[1] ?? "";
  if ((routing.locales as readonly string[]).includes(maybeLocale)) {
    const rest = `/${parts.slice(2).join("/")}` || "/";
    return rest.replace(/\/$/, "") || "/";
  }
  return pathOnly.replace(/\/$/, "") || "/";
}

export function localePrefixedPath(locale: string, path: string): string {
  const safe = path.startsWith("/") ? path : `/${path}`;
  if (safe === `/${locale}` || safe.startsWith(`/${locale}/`)) return safe;
  if (safe === "/") return `/${locale}`;
  return `/${locale}${safe}`;
}

export function workspaceDeskHref(slug: string): string {
  return `/t/${encodeURIComponent(slug)}/chat`;
}

export function isWorkspacePath(pathname: string): boolean {
  const path = stripLocalePrefix(pathname);
  return path === "/chat" || path.startsWith("/t/");
}

/** Home or default admin index — not a deep link the user asked for. */
export function isGenericAdminLanding(pathname: string): boolean {
  const path = stripLocalePrefix(pathname);
  return (
    path === "/" ||
    path === "/admin" ||
    path === "/admin/workflows" ||
    path === "/admin/agents"
  );
}

export function orgRoleFromAccessToken(
  token?: string | null,
): string | undefined {
  if (!token) return undefined;
  try {
    const claims = decodeJwtPayload(token);
    return bestOrgRole(claims.org_role) ?? firstStringClaim(claims.org_role);
  } catch {
    return undefined;
  }
}

export function claimsAllowAdmin(input: {
  accessToken?: string | null;
  orgRole?: string | null;
}): boolean {
  if (input.accessToken && tokenIsPlatformAdmin(input.accessToken)) {
    return true;
  }
  const role =
    orgRoleFromAccessToken(input.accessToken) ?? bestOrgRole(input.orgRole);
  return Boolean(role && ADMIN_ORG_ROLES.has(role));
}

/**
 * Admin layout gate. JWT org_role can lag Atlas membership (Owner is
 * tenant_admin in the workspace even when the token still says org:member).
 */
export function allowsAdminApp(input: {
  accessToken?: string | null;
  orgRole?: string | null;
  workspace?: {
    can_administer?: boolean;
    role?: string | null;
  } | null;
}): boolean {
  if (claimsAllowAdmin(input)) return true;
  return canOpenOrgAdmin(input.workspace);
}

export function defaultAdminLandingHref(input: {
  accessToken?: string | null;
}): string {
  if (input.accessToken && tokenIsPlatformAdmin(input.accessToken)) {
    return PLATFORM_TENANTS_HREF;
  }
  return ORG_ADMIN_HREF;
}

/**
 * Choose the post-login URL from the JWT role (available after password grant)
 * so end users never land on `/admin` first.
 */
export async function resolvePostLoginHref(input: {
  accessToken: string;
  orgRole?: string | null;
  callbackUrl?: string | null;
  loadWorkspace?: () => Promise<PostLoginWorkspace | null>;
}): Promise<string> {
  const callback = safeAuthCallbackUrl(input.callbackUrl, "");
  if (callback && isWorkspacePath(callback)) {
    return callback;
  }

  const jwtAdmin = claimsAllowAdmin(input);
  let workspace: PostLoginWorkspace | null = null;
  if (!jwtAdmin && input.loadWorkspace) {
    try {
      workspace = await input.loadWorkspace();
    } catch {
      workspace = null;
    }
  }

  const canAdmin =
    workspace?.can_administer === false ? false : jwtAdmin;

  if (!canAdmin) {
    const slug = workspace?.slug?.trim();
    if (slug) return workspaceDeskHref(slug);
    return "/chat";
  }

  if (callback && !isGenericAdminLanding(callback)) {
    return callback;
  }

  return defaultAdminLandingHref(input);
}
