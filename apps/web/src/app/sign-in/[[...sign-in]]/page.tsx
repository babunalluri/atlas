"use client";

import { Suspense } from "react";
import { signIn } from "next-auth/react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { safeAuthCallbackUrl } from "@/lib/auth/callback-url";

function SignInActions() {
  const searchParams = useSearchParams();
  const callbackUrl = safeAuthCallbackUrl(searchParams.get("callbackUrl"));

  return (
    <button
      type="button"
      onClick={() => signIn("keycloak", { callbackUrl })}
      className="rounded-md bg-ink px-4 py-3 text-sm font-medium text-canvas"
    >
      Continue with Keycloak
    </button>
  );
}

export default function SignInPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-6 px-6">
      <div>
        <p className="text-sm font-medium text-accent">Atlas</p>
        <h1 className="mt-2 font-display text-3xl text-ink">Sign in</h1>
        <p className="mt-2 text-sm text-slate-muted">
          Staff authentication uses self-hosted Keycloak (open source). No Clerk
          subscription required.
        </p>
      </div>
      <Suspense
        fallback={
          <button
            type="button"
            disabled
            className="rounded-md bg-ink px-4 py-3 text-sm font-medium text-canvas opacity-70"
          >
            Continue with Keycloak
          </button>
        }
      >
        <SignInActions />
      </Suspense>
      <p className="text-xs text-slate-muted">
        Dev users: <code>admin@atlas.local</code> / <code>atlas-admin</code> ·{" "}
        <code>ops@acme.atlas.local</code> / <code>atlas-acme</code>
      </p>
      <Link href="/" className="text-sm text-accent underline">
        Back to home
      </Link>
    </main>
  );
}
