import { NextResponse } from "next/server";

import { auth } from "@/auth";
import { staffAuthConfigured } from "@/lib/auth/staff-auth-config";

const publicPrefixes = ["/", "/sign-in", "/sign-up", "/t/", "/embed/", "/api/auth"];

function isPublic(pathname: string): boolean {
  if (pathname === "/") return true;
  return publicPrefixes.some(
    (prefix) => prefix !== "/" && pathname.startsWith(prefix),
  );
}

const allowDevAuthBypass =
  process.env.NEXT_PUBLIC_DEV_AUTH === "true" &&
  (process.env.NODE_ENV ?? "development") === "development";

function denyMisconfiguredAuth() {
  return new NextResponse(
    "Authentication is not configured. Set AUTH_SECRET, AUTH_KEYCLOAK_ID, AUTH_KEYCLOAK_SECRET, and AUTH_KEYCLOAK_ISSUER to non-placeholder values.",
    { status: 503 },
  );
}

export default auth((request) => {
  const { pathname } = request.nextUrl;
  if (isPublic(pathname) || allowDevAuthBypass) {
    return NextResponse.next();
  }
  // Fail closed outside development when IdP/session secrets are missing or
  // still set to repo-committed placeholders (prevents forged session cookies).
  if (!staffAuthConfigured()) {
    return denyMisconfiguredAuth();
  }
  if (!request.auth) {
    const signIn = new URL("/sign-in", request.nextUrl.origin);
    const returnTo = `${request.nextUrl.pathname}${request.nextUrl.search}`;
    signIn.searchParams.set("callbackUrl", returnTo || "/admin");
    return NextResponse.redirect(signIn);
  }
  return NextResponse.next();
});

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
