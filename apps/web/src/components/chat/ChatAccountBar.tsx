"use client";

import type { Session } from "next-auth";
import { useSession } from "next-auth/react";
import { useLocale } from "next-intl";
import { useEffect, useState } from "react";

import { useSignInModal } from "@/components/auth/SignInModalProvider";
import { WorkspaceProfileMenu } from "@/components/chat/WorkspaceProfileMenu";
import { WorkspaceTracesButton } from "@/components/chat/WorkspaceTracesPanel";
import { Link } from "@/i18n/navigation";
import { getWorkspaceInfo } from "@/lib/api/admin";
import {
  sessionLooksSignedIn,
  visibleAuthSession,
} from "@/lib/auth/auth-session";
import { canOpenOrgAdmin, ORG_ADMIN_HREF } from "@/lib/auth/desk-admin";
import { localePrefixedPath } from "@/lib/auth/post-login";
import { useAgentOsToken } from "@/lib/auth/token";
import { Button, buttonClassName } from "@/components/ui/Button";

/**
 * Compact account control for hosted chat and customer desks.
 */
export function ChatAccountBar({
  tenantSlug,
  signInRedirect,
  serverSession = null,
}: {
  tenantSlug: string;
  signInRedirect: string;
  serverSession?: Session | null;
}) {
  const locale = useLocale();
  const { data: clientSession, status } = useSession();
  const { openSignIn } = useSignInModal();
  const { getAccessToken, isLoaded, isSignedIn } = useAgentOsToken();
  const session = visibleAuthSession(status, clientSession, serverSession);
  const [canAdminister, setCanAdminister] = useState(false);

  useEffect(() => {
    if (!isLoaded || !isSignedIn) {
      setCanAdminister(false);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const token = await getAccessToken();
        const workspace = await getWorkspaceInfo(token);
        if (!cancelled) setCanAdminister(canOpenOrgAdmin(workspace));
      } catch {
        if (!cancelled) setCanAdminister(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- getAccessToken is stable via callback
  }, [isLoaded, isSignedIn]);

  if (status === "loading" && !sessionLooksSignedIn(serverSession)) {
    return <span className="size-8 rounded-full bg-raised" />;
  }

  if (!sessionLooksSignedIn(session)) {
    return (
      <Button
        type="button"
        size="sm"
        variant="secondary"
        onClick={() =>
          openSignIn({
            callbackUrl: signInRedirect || `/t/${tenantSlug}/chat`,
          })
        }
      >
        Sign in
      </Button>
    );
  }

  return (
    <div className="flex min-w-0 items-center gap-2">
      <WorkspaceTracesButton />
      <WorkspaceProfileMenu user={session?.user} />
      {canAdminister ? (
        <a
          href={localePrefixedPath(locale, ORG_ADMIN_HREF)}
          title="Open organization admin"
          className={buttonClassName({ variant: "secondary", size: "sm" })}
        >
          Admin
        </a>
      ) : null}
    </div>
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
