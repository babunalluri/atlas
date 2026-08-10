/**
 * Only allow same-origin relative paths as post-login redirects.
 * Absolute same-origin URLs are reduced to path+query+hash.
 */
export function safeAuthCallbackUrl(
  raw: string | null | undefined,
  fallback = "/admin",
): string {
  if (!raw) return fallback;
  const value = raw.trim();
  if (value.startsWith("/") && !value.startsWith("//")) {
    return value;
  }
  try {
    const url = new URL(value);
    const appBase =
      process.env.NEXT_PUBLIC_APP_URL?.trim() ||
      process.env.AUTH_URL?.trim() ||
      "";
    const allowedOrigins = new Set<string>();
    if (appBase) {
      try {
        allowedOrigins.add(new URL(appBase).origin);
      } catch {
        // ignore invalid app base
      }
    }
    if (typeof window !== "undefined") {
      allowedOrigins.add(window.location.origin);
    }
    if (allowedOrigins.has(url.origin)) {
      return `${url.pathname}${url.search}${url.hash}` || fallback;
    }
  } catch {
    // fall through
  }
  return fallback;
}
