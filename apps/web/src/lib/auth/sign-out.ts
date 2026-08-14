import { locales } from "@/i18n/routing";

const SESSION_COOKIE_BASES = [
  "authjs.session-token",
  "authjs.callback-url",
  "__Secure-authjs.session-token",
  "__Secure-authjs.callback-url",
  "__Host-authjs.session-token",
  "next-auth.session-token",
  "next-auth.callback-url",
  "__Secure-next-auth.session-token",
  "__Secure-next-auth.callback-url",
] as const;

/**
 * Public signed-out home. Never return a workspace/desk/admin path — those
 * shells treat a leftover session cookie as "still logged in".
 */
export function atlasSignedOutHomePath(callbackUrl = "/"): string {
  const fallback = "/";
  const raw = (callbackUrl ?? "").trim() || fallback;
  let pathname = raw;
  if (/^https?:\/\//i.test(raw)) {
    try {
      pathname = new URL(raw).pathname || fallback;
    } catch {
      return fallback;
    }
  } else if (!raw.startsWith("/") || raw.startsWith("//")) {
    return fallback;
  }
  const segment = pathname.split("/").filter(Boolean)[0];
  if (segment && (locales as readonly string[]).includes(segment)) {
    return `/${segment}`;
  }
  return fallback;
}

export function isAtlasAuthCookieName(name: string): boolean {
  const n = name.trim();
  return SESSION_COOKIE_BASES.some(
    (base) => n === base || n.startsWith(`${base}.`),
  );
}

export function atlasAuthCookieNamesFromHeader(cookieHeader: string): string[] {
  return cookieHeader
    .split(";")
    .map((part) => part.trim().split("=")[0] ?? "")
    .filter((name) => name && isAtlasAuthCookieName(name));
}

export function cookieExpireAssignment(name: string): string {
  return `${name}=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT; SameSite=Lax`;
}

/**
 * Drop client-visible Auth.js cookies (chunked session tokens, callback-url).
 * HttpOnly session cookies are cleared by NextAuth `signOut`; this covers
 * leftovers the POST does not expire.
 */
export function expireAtlasAuthCookies(
  cookieHeader: string = typeof document === "undefined" ? "" : document.cookie,
  assign: (value: string) => void = (value) => {
    if (typeof document !== "undefined") document.cookie = value;
  },
): string[] {
  const names = atlasAuthCookieNamesFromHeader(cookieHeader);
  for (const name of names) {
    assign(cookieExpireAssignment(name));
  }
  return names;
}
