"use client";

import type { Session } from "next-auth";
import { useSession } from "next-auth/react";
import { useLocale, useTranslations } from "next-intl";

import { UserIdentityChip } from "@/components/auth/UserIdentityChip";
import { Button } from "@/components/ui/Button";
import { SignOutIcon } from "@/components/ui/icons";
import {
  sessionLooksSignedIn,
  visibleAuthSession,
} from "@/lib/auth/auth-session";
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
  const session = visibleAuthSession(status, clientSession, serverSession);

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
    <div className="flex min-w-0 items-center gap-2">
      <UserIdentityChip user={session?.user} />
      <Button
        type="button"
        variant="secondary"
        size="sm"
        icon={<SignOutIcon />}
        onClick={() => void signOutFederated(`/${locale}`)}
      >
        {t("signOut")}
      </Button>
    </div>
  );
}
