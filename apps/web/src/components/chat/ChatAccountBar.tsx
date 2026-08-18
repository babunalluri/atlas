"use client";

import type { Session } from "next-auth";
import { useSession } from "next-auth/react";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import { useSignInModal } from "@/components/auth/SignInModalProvider";
import { WorkspaceProfileMenu } from "@/components/chat/WorkspaceProfileMenu";
import { WorkspaceTracesButton } from "@/components/chat/WorkspaceTracesPanel";
import {
  ThemeToggle,
  useSurfaceTheme,
  type SurfaceTheme,
} from "@/components/layout/ThemeToggle";
import { NotificationBell } from "@/components/notifications/NotificationBell";
import { Link } from "@/i18n/navigation";
import { getWorkspaceInfo } from "@/lib/api/admin";
import {
  sessionLooksSignedIn,
  visibleAuthSession,
} from "@/lib/auth/auth-session";
import { canOpenOrgAdmin, ORG_ADMIN_HREF } from "@/lib/auth/desk-admin";
import { signOutFederated } from "@/lib/auth/federated-signout";
import { localePrefixedPath } from "@/lib/auth/post-login";
import { useAgentOsToken } from "@/lib/auth/token";
import { Button, buttonClassName } from "@/components/ui/Button";
import {
  AdminIcon,
  BUTTON_ICON_CLASS,
  SignInIcon,
  SignOutIcon,
} from "@/components/ui/icons";

/**
 * Compact account control for hosted chat and customer desks.
 */
export function ChatAccountBar({
  tenantSlug,
  signInRedirect,
  serverSession = null,
  theme: themeProp,
  onThemeChange,
}: {
  tenantSlug: string;
  signInRedirect: string;
  serverSession?: Session | null;
  /** When omitted, theme is managed locally (pages that do not set data-theme). */
  theme?: SurfaceTheme;
  onThemeChange?: (theme: SurfaceTheme) => void;
}) {
  const t = useTranslations("common");
  const locale = useLocale();
  const { data: clientSession, status } = useSession();
  const { openSignIn } = useSignInModal();
  const { getAccessToken, isLoaded, isSignedIn } = useAgentOsToken();
  const session = visibleAuthSession(status, clientSession, serverSession);
  const [canAdminister, setCanAdminister] = useState(false);
  const localTheme = useSurfaceTheme("workspace");
  const theme = themeProp ?? localTheme.theme;
  const changeTheme = onThemeChange ?? localTheme.changeTheme;

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
        icon={<SignInIcon />}
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
    <div className="flex min-w-0 flex-wrap items-center justify-end gap-2">
      {canAdminister ? (
        <a
          href={localePrefixedPath(locale, ORG_ADMIN_HREF)}
          title="Open organization admin"
          className={buttonClassName({ variant: "secondary", size: "sm" })}
        >
          <AdminIcon className={BUTTON_ICON_CLASS} />
          Admin
        </a>
      ) : null}
      <ThemeToggle theme={theme} onChange={changeTheme} />
      <NotificationBell />
      <WorkspaceTracesButton />
      <WorkspaceProfileMenu user={session?.user} tenantSlug={tenantSlug} />
      <Button
        type="button"
        size="sm"
        variant="secondary"
        icon={<SignOutIcon />}
        onClick={() => void signOutFederated(`/${locale}`)}
      >
        {t("signOut")}
      </Button>
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
