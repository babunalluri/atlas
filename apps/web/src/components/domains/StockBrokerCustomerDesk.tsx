"use client";

import type { Session } from "next-auth";
import { useEffect, useState } from "react";

import { ChatAccountBar } from "@/components/chat/ChatAccountBar";
import { DeskChatDraftProvider } from "@/components/domains/DeskChatDraftContext";
import { StockBrokerWorkspace } from "@/components/domains/StockBrokerWorkspace";
import { useSurfaceTheme } from "@/components/layout/ThemeToggle";
import {
  getAdminDesk,
  getCustomerDesk,
  getWorkspaceInfo,
  type DomainDashboard,
} from "@/lib/api/admin";
import type { TenantBranding } from "@/lib/api/types";
import { canOpenOrgAdmin } from "@/lib/auth/desk-admin";
import { useAgentOsToken } from "@/lib/auth/token";
import { useRouter } from "@/i18n/navigation";

export function StockBrokerCustomerDesk({
  tenant,
  serverSession = null,
}: {
  tenant: TenantBranding;
  serverSession?: Session | null;
}) {
  const router = useRouter();
  const { getAccessToken, isLoaded, isSignedIn } = useAgentOsToken();
  const { theme, dark, changeTheme } = useSurfaceTheme("workspace");
  const [data, setData] = useState<DomainDashboard | null>(null);
  const [isAdminDesk, setIsAdminDesk] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadDesk(snapshot: boolean, adminDesk: boolean) {
    const token = await getAccessToken();
    if (!token) throw new Error("Sign in to open your trading desk.");
    if (adminDesk) {
      return getAdminDesk(token, snapshot);
    }
    return getCustomerDesk(token, snapshot);
  }

  useEffect(() => {
    if (!isLoaded) return;
    if (!isSignedIn) {
      router.replace(
        `/sign-in?redirect_url=${encodeURIComponent(`/t/${tenant.slug}/chat`)}`,
      );
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const token = await getAccessToken();
        if (!token || cancelled) return;
        const workspace = await getWorkspaceInfo(token);
        const adminDesk = canOpenOrgAdmin(workspace);
        const next = await loadDesk(false, adminDesk);
        if (!cancelled) {
          setIsAdminDesk(adminDesk);
          setData(next);
          setError(null);
        }
      } catch (reason) {
        if (!cancelled) {
          setError(
            reason instanceof Error ? reason.message : "Could not load the trading desk",
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoaded, isSignedIn, tenant.slug]);

  async function refresh() {
    setRefreshing(true);
    setError(null);
    try {
      setData(await loadDesk(true, isAdminDesk));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Refresh failed");
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <DeskChatDraftProvider targets={data?.chat_targets ?? []}>
      <div
        data-theme={dark ? "dark" : undefined}
        className="app-canvas flex h-dvh min-h-0 flex-col text-ink"
      >
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-line px-4 py-2.5">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-teal">
            {tenant.name}
          </p>
          <p className="text-sm font-medium">Trading desk</p>
        </div>
        <div className="flex min-w-0 items-center gap-2">
          <ChatAccountBar
            tenantSlug={tenant.slug}
            signInRedirect={`/t/${tenant.slug}/chat`}
            serverSession={serverSession}
            theme={theme}
            onThemeChange={changeTheme}
          />
        </div>
      </header>
      {error ? (
        <p className="shrink-0 border-b border-rose/30 bg-rose/10 px-4 py-2 text-xs text-rose">
          {error}
        </p>
      ) : null}
      <div className="min-h-0 flex-1">
        {!isLoaded || !isSignedIn ? (
          <p className="px-5 py-10 text-sm text-slate-muted">Redirecting to sign in…</p>
        ) : data ? (
          <StockBrokerWorkspace
            data={data}
            refreshing={refreshing}
            onRefresh={() => void refresh()}
            variant={isAdminDesk ? "admin" : "customer"}
            deskTitle="Trading desk"
          />
        ) : (
          <p className="px-5 py-10 text-sm text-slate-muted">Loading your desk…</p>
        )}
      </div>
      </div>
    </DeskChatDraftProvider>
  );
}
