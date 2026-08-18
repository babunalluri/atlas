"use client";

import type { Session } from "next-auth";
import { useTranslations } from "next-intl";
import { useEffect } from "react";
import { ChatAccountBar } from "@/components/chat/ChatAccountBar";
import {
  useSurfaceTheme,
} from "@/components/layout/ThemeToggle";
import { LanguageSwitcher } from "@/components/i18n/LanguageSwitcher";
import { UserSelfVaultEditor } from "@/components/vault/UserSelfVaultEditor";
import { Link, useRouter } from "@/i18n/navigation";
import type { TenantBranding } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";

export function WorkspaceSettingsPage({
  tenant,
  serverSession = null,
}: {
  tenant: TenantBranding;
  serverSession?: Session | null;
}) {
  const t = useTranslations("common");
  const router = useRouter();
  const { isLoaded, isSignedIn } = useAgentOsToken();
  const { theme, dark, changeTheme } = useSurfaceTheme("workspace");

  useEffect(() => {
    if (!isLoaded) return;
    if (!isSignedIn) {
      router.replace(
        `/sign-in?redirect_url=${encodeURIComponent(`/t/${tenant.slug}/settings`)}`,
      );
    }
  }, [isLoaded, isSignedIn, router, tenant.slug]);

  return (
    <div
      data-theme={dark ? "dark" : undefined}
      className="app-canvas flex min-h-dvh flex-col text-ink"
    >
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-line px-4 py-2.5">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-teal">
            {tenant.name}
          </p>
          <p className="text-sm font-medium">{t("settings.title")}</p>
        </div>
        <div className="flex min-w-0 items-center gap-2">
          <ChatAccountBar
            tenantSlug={tenant.slug}
            signInRedirect={`/t/${tenant.slug}/settings`}
            serverSession={serverSession}
            theme={theme}
            onThemeChange={changeTheme}
          />
        </div>
      </header>

      <main className="mx-auto w-full max-w-2xl flex-1 px-4 py-8">
        {!isLoaded || !isSignedIn ? (
          <p className="text-sm text-slate-muted">{t("loading")}</p>
        ) : (
          <>
            <Link
              href={`/t/${tenant.slug}/chat`}
              className="text-sm font-medium text-teal hover:underline"
            >
              ← {t("backToWorkspace")}
            </Link>

            <h1 className="mt-4 font-display text-2xl font-semibold tracking-tight">
              {t("settings.title")}
            </h1>
            <p className="mt-1 text-sm text-slate-muted">
              {t("settings.description")}
            </p>

            <section className="mt-8 surface-panel rounded-xl p-5">
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
              <p className="mt-1 text-xs text-slate-muted">
                {t("profile.vaultHint")}
              </p>
              <div className="mt-3">
                <UserSelfVaultEditor />
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  );
}
