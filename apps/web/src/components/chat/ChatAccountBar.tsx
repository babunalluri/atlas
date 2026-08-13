"use client";

import { signIn, useSession } from "next-auth/react";
import { useLocale } from "next-intl";

import { Link } from "@/i18n/navigation";
import { signOutFederated } from "@/lib/auth/federated-signout";
import { Button, buttonClassName } from "@/components/ui/Button";

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
    return <span className="size-8 rounded-full bg-raised" />;
  }

  if (!session) {
    return (
      <Button
        type="button"
        size="sm"
        variant="secondary"
        onClick={() =>
          signIn("keycloak", {
            callbackUrl: signInRedirect || `/t/${tenantSlug}/chat`,
          })
        }
      >
        Sign in
      </Button>
    );
  }

  return (
    <Button
      type="button"
      size="sm"
      variant="secondary"
      onClick={() => void signOutFederated(`/${locale}/sign-in`)}
      title={session.user?.email || "Signed in"}
    >
      Sign out
    </Button>
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
      className={buttonClassName({ variant: "secondary", size: "sm" })}
    >
      Account
    </Link>
  );
}
