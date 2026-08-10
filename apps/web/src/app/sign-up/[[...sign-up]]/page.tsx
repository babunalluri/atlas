"use client";

import { Suspense } from "react";
import { signIn } from "next-auth/react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { safeAuthCallbackUrl } from "@/lib/auth/callback-url";

function SignUpActions() {
  const searchParams = useSearchParams();
  const callbackUrl = safeAuthCallbackUrl(searchParams.get("callbackUrl"));

  return (
    <button
      type="button"
      onClick={() => signIn("keycloak", { callbackUrl, redirect: true })}
      className="rounded-md bg-ink px-4 py-3 text-sm font-medium text-canvas"
    >
      Register via Keycloak
    </button>
  );
}

export default function SignUpPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-6 px-6">
      <div>
        <p className="text-sm font-medium text-accent">Atlas</p>
        <h1 className="mt-2 font-display text-3xl text-ink">Create account</h1>
        <p className="mt-2 text-sm text-slate-muted">
          Registration is handled by Keycloak. After signup, an Atlas admin must
          attach your user to a workspace organization/group.
        </p>
      </div>
      <Suspense
        fallback={
          <button
            type="button"
            disabled
            className="rounded-md bg-ink px-4 py-3 text-sm font-medium text-canvas opacity-70"
          >
            Register via Keycloak
          </button>
        }
      >
        <SignUpActions />
      </Suspense>
      <Link href="/sign-in" className="text-sm text-accent underline">
        Already have an account? Sign in
      </Link>
    </main>
  );
}
