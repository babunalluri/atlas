"use client";

import Link from "next/link";
import { signIn, useSession } from "next-auth/react";

import { signOutFederated } from "@/lib/auth/federated-signout";

export function AuthControls() {
  const { data: session, status } = useSession();

  if (status === "loading") {
    return (
      <span className="rounded-md border border-line bg-raised px-3 py-2 text-sm text-slate-muted">
        Loading…
      </span>
    );
  }

  if (!session) {
    return (
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => signIn("keycloak", { callbackUrl: "/admin" })}
          className="rounded-md border border-line bg-raised px-3 py-2 text-sm font-medium text-ink"
        >
          Sign in
        </button>
        <Link
          href="/sign-up"
          className="rounded-md bg-ink px-3 py-2 text-sm font-medium text-canvas"
        >
          Sign up
        </Link>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <span className="hidden text-sm text-slate-muted sm:inline">
        {session.user?.email || session.user?.name || "Signed in"}
      </span>
      <button
        type="button"
        onClick={() => void signOutFederated("/")}
        className="rounded-md border border-line bg-raised px-3 py-2 text-sm font-medium text-ink"
      >
        Sign out
      </button>
    </div>
  );
}
