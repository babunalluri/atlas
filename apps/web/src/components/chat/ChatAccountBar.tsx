"use client";

import {
  SignedIn,
  SignedOut,
  SignInButton,
  UserButton,
  useAuth,
} from "@clerk/nextjs";
import Link from "next/link";

/**
 * Compact account control for hosted chat — avatar only when signed in.
 */
export function ChatAccountBar({
  tenantSlug,
  signInRedirect,
}: {
  tenantSlug: string;
  signInRedirect: string;
}) {
  const publishableKey = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
  const clerkReady =
    !!publishableKey && !publishableKey.includes("replace_me");

  if (!clerkReady) {
    return (
      <Link
        href={`/sign-in?redirect_url=${encodeURIComponent(signInRedirect)}`}
        className="rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 text-xs font-medium text-white/80"
      >
        Account
      </Link>
    );
  }

  return <ClerkChatAccount tenantSlug={tenantSlug} signInRedirect={signInRedirect} />;
}

function ClerkChatAccount({
  tenantSlug,
  signInRedirect,
}: {
  tenantSlug: string;
  signInRedirect: string;
}) {
  const { isLoaded } = useAuth();

  if (!isLoaded) {
    return <span className="size-8 rounded-full bg-white/10" />;
  }

  return (
    <div className="flex items-center">
      <SignedOut>
        <SignInButton mode="modal" forceRedirectUrl={signInRedirect}>
          <button
            type="button"
            className="rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 text-xs font-medium text-white/85 hover:bg-white/10"
          >
            Sign in
          </button>
        </SignInButton>
      </SignedOut>
      <SignedIn>
        <UserButton
          afterSignOutUrl={`/t/${tenantSlug}/chat`}
          appearance={{
            elements: {
              avatarBox: "size-8",
            },
          }}
        />
      </SignedIn>
    </div>
  );
}
