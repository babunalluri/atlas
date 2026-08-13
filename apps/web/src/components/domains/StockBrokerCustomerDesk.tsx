"use client";

import { useEffect, useState } from "react";

import { ChatAccountBar } from "@/components/chat/ChatAccountBar";
import { NotificationBell } from "@/components/notifications/NotificationBell";
import { StockBrokerWorkspace } from "@/components/domains/StockBrokerWorkspace";
import {
  ThemeToggle,
  useSurfaceTheme,
} from "@/components/layout/ThemeToggle";
import { getCustomerDesk, type DomainDashboard } from "@/lib/api/admin";
import type { TenantBranding } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { useRouter } from "@/i18n/navigation";

export function StockBrokerCustomerDesk({ tenant }: { tenant: TenantBranding }) {
  const router = useRouter();
  const { getAccessToken, isLoaded, isSignedIn } = useAgentOsToken();
  const { theme, dark, changeTheme } = useSurfaceTheme("workspace");
  const [data, setData] = useState<DomainDashboard | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load(snapshot: boolean) {
    const token = await getAccessToken();
    if (!token) throw new Error("Sign in to open your trading desk.");
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
        const next = await load(false);
        if (!cancelled) {
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
      setData(await load(true));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Refresh failed");
    } finally {
      setRefreshing(false);
    }
  }

  return (
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
        <div className="flex items-center gap-2">
          <NotificationBell />
          <ThemeToggle theme={theme} onChange={changeTheme} />
          <ChatAccountBar
            tenantSlug={tenant.slug}
            signInRedirect={`/t/${tenant.slug}/chat`}
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
            variant="customer"
          />
        ) : (
          <p className="px-5 py-10 text-sm text-slate-muted">Loading your desk…</p>
        )}
      </div>
    </div>
  );
}
