"use client";

import type { Session } from "next-auth";
import { useSession } from "next-auth/react";
import { useLocale, useTranslations } from "next-intl";

import { sessionLooksSignedIn } from "@/lib/auth/auth-session";
import { signOutFederated } from "@/lib/auth/federated-signout";

function devAuthEnabled(): boolean {
  return process.env.NEXT_PUBLIC_DEV_AUTH === "true";
}

export function AuthControls({
  serverSession = null,
}: {
  serverSession?: Session | null;
}) {
  const t = useTranslations("common");
  const locale = useLocale();
  const { data: clientSession, status } = useSession();
  const session = clientSession ?? serverSession;

  if (status === "loading" && !sessionLooksSignedIn(serverSession)) {
    return (
      <span className="rounded-md border border-line bg-raised px-3 py-2 text-sm text-slate-muted">
        {t("loading")}
      </span>
    );
  }

  // Admin-only control: never render Sign in. Missing session is a redirect.
  if (!sessionLooksSignedIn(session) && !devAuthEnabled()) {
    return (
      <span className="rounded-md border border-line bg-raised px-3 py-2 text-sm text-slate-muted">
        {t("loading")}
      </span>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <span className="hidden text-sm text-slate-muted sm:inline">
        {session?.user?.email || session?.user?.name || "Signed in"}
      </span>
      <button
        type="button"
        onClick={() => void signOutFederated(`/${locale}`)}
        className="rounded-md border border-line bg-raised px-3 py-2 text-sm font-medium text-ink"
      >
        {t("signOut")}
      </button>
    </div>
  );
}
