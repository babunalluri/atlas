"use client";

import { useRouter } from "@/i18n/navigation";
import { useEffect, useState } from "react";

import { getOnboardingStatus, getWorkspaceInfo } from "@/lib/api/admin";
import { useAgentOsToken } from "@/lib/auth/token";

/**
 * Resolves the signed-in user's tenant and redirects into their workspace:
 * `/t/{slug}/workspace` for trading desks (instrument-first), `/t/{slug}/chat`
 * otherwise. Kept for deep links and bookmarks; home no longer promotes this.
 */
export default function ChatEntryPage() {
  const router = useRouter();
  const { getAccessToken, isLoaded, isSignedIn } = useAgentOsToken();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isLoaded) return;
    if (!isSignedIn) {
      router.replace(
        `/sign-in?redirect_url=${encodeURIComponent("/chat")}`,
      );
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const token = await getAccessToken();
        if (!token || cancelled) return;
        const status = await getOnboardingStatus(token);
        if (cancelled) return;
        if (!status.provisioned) {
          router.replace("/admin/onboarding");
          return;
        }
        const workspace = await getWorkspaceInfo(token);
        if (cancelled) return;
        const slug = workspace.slug || status.tenant_slug;
        if (!slug) {
          setError("Workspace slug is missing.");
          return;
        }
        // Trading desks are instrument-first: land on the instrument list, not
        // a desk already pinned to one instrument. Other domains keep chat.
        router.replace(
          workspace.domain === "stock_broker"
            ? `/t/${slug}/workspace`
            : `/t/${slug}/chat`,
        );
      } catch (reason) {
        if (!cancelled) {
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not open your workspace",
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getAccessToken, isLoaded, isSignedIn, router]);

  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <p className="text-sm text-slate-muted">
        {error ?? "Opening your workspace…"}
      </p>
    </main>
  );
}
