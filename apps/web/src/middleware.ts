import createMiddleware from "next-intl/middleware";
import { NextResponse } from "next/server";

import { auth } from "@/auth";
import { routing } from "@/i18n/routing";
import { staffAuthConfigured } from "@/lib/auth/staff-auth-config";

const intlMiddleware = createMiddleware(routing);

const allowDevAuthBypass =
  process.env.NEXT_PUBLIC_DEV_AUTH === "true" &&
  (process.env.NODE_ENV ?? "development") === "development";

function denyMisconfiguredAuth() {
  return new NextResponse(
    "Authentication is not configured. Set AUTH_SECRET, AUTH_KEYCLOAK_ID, AUTH_KEYCLOAK_SECRET, and AUTH_KEYCLOAK_ISSUER to non-placeholder values.",
    { status: 503 },
  );
}

function stripLocale(pathname: string): { locale: string | null; path: string } {
  const parts = pathname.split("/");
  const maybeLocale = parts[1] ?? "";
  if ((routing.locales as readonly string[]).includes(maybeLocale)) {
    const rest = `/${parts.slice(2).join("/")}` || "/";
    return { locale: maybeLocale, path: rest.replace(/\/$/, "") || "/" };
  }
  return { locale: null, path: pathname };
}

function isPublicPath(pathWithoutLocale: string): boolean {
  if (pathWithoutLocale === "/") return true;
  const publicPrefixes = [
    "/sign-in",
    "/sign-up",
    "/t/",
    "/embed/",
    "/chat",
    "/api/auth",
  ];
  return publicPrefixes.some(
    (prefix) =>
      pathWithoutLocale === prefix.replace(/\/$/, "") ||
      pathWithoutLocale.startsWith(prefix),
  );
}

export default auth((request) => {
  const { pathname } = request.nextUrl;

  // Auth.js / API routes skip locale handling.
  if (pathname.startsWith("/api/")) {
    return NextResponse.next();
  }

  const intlResponse = intlMiddleware(request);

  const { locale, path } = stripLocale(pathname);
  const pathForAuth = locale ? path : pathname;

  if (isPublicPath(pathForAuth) || allowDevAuthBypass) {
    return intlResponse;
  }

  if (!staffAuthConfigured()) {
    return denyMisconfiguredAuth();
  }

  if (!request.auth) {
    const signInLocale = locale ?? routing.defaultLocale;
    const signIn = new URL(`/${signInLocale}/sign-in`, request.nextUrl.origin);
    const returnTo = `${request.nextUrl.pathname}${request.nextUrl.search}`;
    signIn.searchParams.set("callbackUrl", returnTo || `/${signInLocale}/admin`);
    return NextResponse.redirect(signIn);
  }

  return intlResponse;
});

export const config = {
  matcher: [
    "/((?!_next|.*\\..*).*)",
  ],
};
