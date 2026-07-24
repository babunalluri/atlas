import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

const isPublicRoute = createRouteMatcher([
  "/",
  "/sign-in(.*)",
  "/sign-up(.*)",
  "/t/(.*)",
  "/embed/(.*)",
]);

const clerkConfigured =
  !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY &&
  !process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY.includes("replace_me");

// When true, admin pages are reachable without a Clerk browser session.
// Keep this false in any shared/staging/production environment.
const devAuthBypass = process.env.NEXT_PUBLIC_DEV_AUTH === "true";

export default clerkConfigured && !devAuthBypass
  ? clerkMiddleware(async (auth, request) => {
      if (isPublicRoute(request)) {
        return NextResponse.next();
      }
      const session = await auth();
      if (!session.userId) {
        // Prefer an explicit sign-in redirect over Clerk's protect rewrite,
        // which can surface as a blank Next.js 404 in local browsers.
        return session.redirectToSignIn({ returnBackUrl: request.url });
      }
      return NextResponse.next();
    })
  : function passthrough() {
      return NextResponse.next();
    };

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
