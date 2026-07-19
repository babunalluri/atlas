"use client";

import {
  SignedIn,
  SignedOut,
  SignInButton,
  SignUpButton,
  UserButton,
  useAuth,
} from "@clerk/nextjs";
import Link from "next/link";

export function AuthControls() {
  const publishableKey = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
  const clerkReady =
    !!publishableKey && !publishableKey.includes("replace_me");

  if (!clerkReady) {
    return (
      <Link
        href="/sign-in"
        className="rounded-md border border-line bg-raised px-3 py-2 text-sm font-medium text-ink"
      >
        Dev account
      </Link>
    );
  }

  return <ClerkAuthControls />;
}

function ClerkAuthControls() {
  const { isLoaded } = useAuth();

  if (!isLoaded) {
    return (
      <span className="rounded-md border border-line bg-raised px-3 py-2 text-sm text-slate-muted">
        Loading…
      </span>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <SignedOut>
        <SignInButton mode="modal">
          <button className="rounded-md border border-line bg-raised px-3 py-2 text-sm font-medium text-ink">
            Sign in
          </button>
        </SignInButton>
        <SignUpButton mode="modal">
          <button className="rounded-md bg-ink px-3 py-2 text-sm font-medium text-canvas">
            Sign up
          </button>
        </SignUpButton>
      </SignedOut>
      <SignedIn>
        <UserButton afterSignOutUrl="/" />
      </SignedIn>
    </div>
  );
}
