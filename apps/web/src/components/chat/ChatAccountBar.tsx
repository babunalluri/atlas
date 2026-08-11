"use client";

import { signIn, useSession } from "next-auth/react";
import { useLocale } from "next-intl";

import { Link } from "@/i18n/navigation";
import { signOutFederated } from "@/lib/auth/federated-signout";

/**
 * Compact account control for hosted chat.
 */
export function ChatAccountBar({
  tenantSlug,
  signInRedirect,
}: {
  tenantSlug: string;
  signInRedirect: string;
}) {
  const locale = useLocale();
  const { data: session, status } = useSession();

  if (status === "loading") {
    return <span className="size-8 rounded-full bg-white/10" />;
  }

  if (!session) {
    return (
      <button
        type="button"
        onClick={() =>
          signIn("keycloak", {
            callbackUrl: signInRedirect || `/t/${tenantSlug}/chat`,
          })
        }
        className="rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 text-xs font-medium text-white/85 hover:bg-white/10"
      >
        Sign in
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={() => void signOutFederated(`/${locale}/sign-in`)}
      className="rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 text-xs font-medium text-white/85 hover:bg-white/10"
      title={session.user?.email || "Signed in"}
    >
      Sign out
    </button>
  );
}

/** Keep a simple link fallback for surfaces that only need navigation. */
export function ChatAccountLink({
  tenantSlug,
}: {
  tenantSlug: string;
}) {
  return (
    <Link
      href={`/sign-in?callbackUrl=${encodeURIComponent(`/t/${tenantSlug}/chat`)}`}
      className="rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 text-xs font-medium text-white/80"
    >
      Account
    </Link>
  );
}
