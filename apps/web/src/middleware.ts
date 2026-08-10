import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

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

// Local-only bypass. Ignored when NODE_ENV is not development.
const allowDevAuthBypass =
  process.env.NEXT_PUBLIC_DEV_AUTH === "true" &&
  (process.env.NODE_ENV ?? "development") === "development";

function denyMisconfiguredAuth() {
  return new NextResponse(
    "Authentication is not configured. Set a real NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY.",
    { status: 503 },
  );
}

export default clerkConfigured
  ? clerkMiddleware(async (auth, request) => {
      if (isPublicRoute(request) || allowDevAuthBypass) {
        return NextResponse.next();
      }
      const session = await auth();
      if (!session.userId) {
        return session.redirectToSignIn({ returnBackUrl: request.url });
      }
      return NextResponse.next();
    })
  : function failClosed(request: NextRequest) {
      // Without Clerk, only public surfaces may load — never /admin.
      if (isPublicRoute(request) || allowDevAuthBypass) {
        return NextResponse.next();
      }
      return denyMisconfiguredAuth();
    };

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
