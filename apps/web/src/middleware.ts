import { NextResponse } from "next/server";

import { auth } from "@/auth";

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

export default auth((request) => {
  const { pathname } = request.nextUrl;
  if (isPublic(pathname) || allowDevAuthBypass) {
    return NextResponse.next();
  }
  if (!request.auth) {
    const signIn = new URL("/sign-in", request.nextUrl.origin);
    signIn.searchParams.set("callbackUrl", request.nextUrl.href);
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
