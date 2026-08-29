"use client";

import type { Session } from "next-auth";
import { useTranslations } from "next-intl";

import { ChatAccountBar } from "@/components/chat/ChatAccountBar";
import { WorkspaceSettingsBody } from "@/components/chat/WorkspaceSettingsBody";
import { useSurfaceTheme } from "@/components/layout/ThemeToggle";
import type { TenantBranding } from "@/lib/api/types";

export function WorkspaceSettingsPage({
  tenant,
  serverSession = null,
  embedded = false,
}: {
  /** Only slug (routing) and name (header) are read, so a dialog host may
      pass just those rather than full branding. */
  tenant: Pick<TenantBranding, "slug"> & Partial<Pick<TenantBranding, "name">>;
  serverSession?: Session | null;
  /**
   * Render just the settings body, for hosts that supply their own chrome
   * (the trader workspace opens this in a dialog). Skips the page shell, the
   * account bar, and the back link — the dialog already provides all three.
   */
  embedded?: boolean;
}) {
  const t = useTranslations("common");
  const { theme, dark, changeTheme } = useSurfaceTheme("workspace");

  if (embedded) {
    return (
      <div className="mx-auto w-full max-w-2xl">
        <WorkspaceSettingsBody tenantSlug={tenant.slug} showPageHeading={false} />
      </div>
    );
  }

  return (
    <div
      data-theme={dark ? "dark" : undefined}
      className="app-canvas flex min-h-dvh flex-col text-ink"
    >
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-line px-4 py-2.5">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-teal">
            {tenant.name ?? tenant.slug}
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
        <WorkspaceSettingsBody tenantSlug={tenant.slug} />
      </main>
    </div>
  );
}
