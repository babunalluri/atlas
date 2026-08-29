"use client";

import { useTranslations } from "next-intl";
import { useEffect } from "react";

import { LanguageSwitcher } from "@/components/i18n/LanguageSwitcher";
import { UserSelfVaultEditor } from "@/components/vault/UserSelfVaultEditor";
import { Link, useRouter } from "@/i18n/navigation";
import { useAgentOsToken } from "@/lib/auth/token";

/**
 * Settings content only — no page shell, no account bar.
 *
 * Kept separate from ``WorkspaceSettingsPage`` so the account bar's gear can
 * open settings in a dialog: the page imports the account bar, and the account
 * bar imports the gear, so a gear that reached for the page would close an
 * import cycle.
 */
export function WorkspaceSettingsBody({
  tenantSlug,
  /**
   * False when a dialog host supplies the chrome: it already has a title and a
   * close control, so repeating them stacked a second, larger "Settings"
   * heading under the dialog's own.
   */
  showPageHeading = true,
}: {
  tenantSlug: string;
  showPageHeading?: boolean;
}) {
  const t = useTranslations("common");
  const router = useRouter();
  const { isLoaded, isSignedIn } = useAgentOsToken();

  useEffect(() => {
    if (!isLoaded) return;
    if (!isSignedIn) {
      router.replace(
        `/sign-in?redirect_url=${encodeURIComponent(`/t/${tenantSlug}/settings`)}`,
      );
    }
  }, [isLoaded, isSignedIn, router, tenantSlug]);

  if (!isLoaded || !isSignedIn) {
    return <p className="text-sm text-slate-muted">{t("loading")}</p>;
  }

  return (
    <>
      {showPageHeading ? (
        <>
          <Link
            href={`/t/${tenantSlug}/chat`}
            className="text-sm font-medium text-teal hover:underline"
          >
            ← {t("backToWorkspace")}
          </Link>

          <h1 className="mt-4 font-display text-2xl font-semibold tracking-tight">
            {t("settings.title")}
          </h1>
        </>
      ) : null}
      <p className={`${showPageHeading ? "mt-1" : ""} text-sm text-slate-muted`}>
        {t("settings.description")}
      </p>

      <section className="mt-6 surface-panel rounded-xl p-5">
        <h2 className="text-sm font-semibold">{t("settings.preferences")}</h2>
        <p className="mt-1 text-xs text-slate-muted">
          {t("settings.languageHint")}
        </p>
        <div className="mt-3 max-w-xs">
          <LanguageSwitcher labeled />
        </div>
      </section>

      <section className="mt-6 surface-panel rounded-xl p-5">
        <h2 className="text-sm font-semibold">{t("settings.vault")}</h2>
        <p className="mt-1 text-xs text-slate-muted">{t("profile.vaultHint")}</p>
        <div className="mt-3">
          <UserSelfVaultEditor />
        </div>
      </section>
    </>
  );
}
