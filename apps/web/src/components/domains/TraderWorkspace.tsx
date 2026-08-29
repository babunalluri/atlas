"use client";

import type { Session } from "next-auth";
import dynamic from "next/dynamic";
import { createPortal } from "react-dom";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ChatAccountBar } from "@/components/chat/ChatAccountBar";
import { useSurfaceTheme } from "@/components/layout/ThemeToggle";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import {
  ChartLineIcon,
  ChevronRightIcon,
  HistoryIcon,
  LayersIcon,
  PlayIcon,
} from "@/components/ui/icons";
import { DeskBooksNav } from "@/components/domains/DeskBooksNav";
import { DeskChatDraftProvider } from "@/components/domains/DeskChatDraftContext";
import { WorkspaceDeskChat } from "@/components/domains/WorkspaceDeskChat";
import { deskChatEmptyCopy } from "@/components/domains/DeskChat";
import { TradingViewButton } from "@/components/domains/CommonInstrumentSetupBar";
import { overlayForTool } from "@/components/domains/lab-sku";
import {
  getCustomerDesk,
  getOptionsLabConfig,
  getOptionsScreener,
  getWorkspaceInfo,
  type DomainDashboard,
  type OptionsScreenerRow,
  type SignalUnderlyingPreset,
} from "@/lib/api/admin";
import { canOpenOrgAdmin } from "@/lib/auth/desk-admin";
import { cn } from "@/lib/utils";
import type { TenantBranding } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { useRouter } from "@/i18n/navigation";

/**
 * Trader workspace — instrument-first entry for traders.
 *
 * Landing: pick an instrument (indices + equity F&O, searchable), then pick a
 * tool. Tools open in a dialog over the workspace so the trader keeps the
 * instrument list behind them. Dedicated `/lab|/signal|/chart/{instrument}`
 * routes remain for deep links and multi-tab use.
 *
 * Replaces the retired three-tab desk, which mounted Signal and Param Chart
 * hidden to keep their SSE alive — the load that starves a single-worker
 * backend. One tool per window instead.
 * See docs/desk-architecture-roadmap.md (Track A / Phase 1).
 */
/**
 * Landing rows are a watchlist, not a trading feed — poll, never 8 Hz SSE.
 *
 * The screener response is cached server-side at the "medium" tier (60s), so
 * polling faster than that returns an identical payload and only burns HTTP
 * round trips against a single-worker backend. Poll at 20s and pause entirely
 * when the tab is hidden — bound cost by viewport, the way Kite does.
 */
const WATCHLIST_POLL_MS = 20_000;

const CHAT_COLLAPSED_KEY = "atlas-desk-chat-collapsed";

/**
 * Desk chat starts collapsed: the instrument list and the tool are what a
 * trader opens the workspace for, and the rail costs a third of the width.
 * An explicit choice is remembered, so "default" only applies until someone
 * opens it.
 */
function useDeskChatCollapsed() {
  const [collapsed, setCollapsedState] = useState(true);

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(CHAT_COLLAPSED_KEY);
      if (stored === "0") setCollapsedState(false);
    } catch {
      // private mode / blocked storage — stay with the default.
    }
  }, []);

  function setCollapsed(next: boolean) {
    setCollapsedState(next);
    try {
      window.localStorage.setItem(CHAT_COLLAPSED_KEY, next ? "1" : "0");
    } catch {
      // private mode / blocked storage
    }
  }

  return { collapsed, setCollapsed };
}

function panelLoader(label: string) {
  const PanelLoading = () => (
    <div className="flex flex-1 items-center justify-center rounded-lg border border-line bg-canvas/40 px-6 py-12 text-sm text-slate-muted">
      Loading {label}…
    </div>
  );
  PanelLoading.displayName = `PanelLoading(${label})`;
  return PanelLoading;
}

// Every panel is lazy: a trader who opens Signal must not download Lab, and the
// landing must download none of them.
const OptionsLabPanel = dynamic(
  () =>
    import("@/components/domains/OptionsLabPanel").then(
      (module) => module.OptionsLabPanel,
    ),
  { ssr: false, loading: panelLoader("Options Lab") },
);

const SignalMetricsPanel = dynamic(
  () =>
    import("@/components/domains/SignalMetricsPanel").then(
      (module) => module.SignalMetricsPanel,
    ),
  { ssr: false, loading: panelLoader("Signal Engine") },
);

const ParamChartPanel = dynamic(
  () =>
    import("@/components/domains/ParamChartPanel").then(
      (module) => module.ParamChartPanel,
    ),
  { ssr: false, loading: panelLoader("Param Chart") },
);

export function TraderWorkspace({
  tenant,
  serverSession = null,
  instrument = null,
  tool = null,
  surface = "lab",
}: {
  tenant: TenantBranding;
  serverSession?: Session | null;
  /** From the route — drives the Lab chain via `?underlying=`. */
  instrument?: string | null;
  /** From `?tool=` — which Lab overlay to open on mount. */
  tool?: string | null;
  /** Which tool owns this window. One tool per window (product rule). */
  surface?: "lab" | "signal" | "chart";
}) {
  const router = useRouter();
  const { getAccessToken, isLoaded, isSignedIn } = useAgentOsToken();
  const { theme, dark, changeTheme } = useSurfaceTheme("workspace");
  const [presets, setPresets] = useState<SignalUnderlyingPreset[] | null>(null);
  const [deskInstrument, setDeskInstrument] = useState<string | null>(null);
  // End-user Lab SKU: Options Lab + Automation only. Signal Engine and Param
  // Chart are operator tools and stay admin-only (desk-architecture-roadmap).
  const [isAdmin, setIsAdmin] = useState(false);
  // Whether the role lookup has answered. Panels wait for it: mounting them
  // read-only and flipping to writable would re-run every stream effect.
  const [adminResolved, setAdminResolved] = useState(false);
  const [desk, setDesk] = useState<DomainDashboard | null>(null);
  const [deskLoading, setDeskLoading] = useState(true);
  const [deskRefreshing, setDeskRefreshing] = useState(false);
  const [deskError, setDeskError] = useState<string | null>(null);
  const { collapsed: chatCollapsed, setCollapsed: setChatCollapsed } =
    useDeskChatCollapsed();
  const [error, setError] = useState<string | null>(null);
  const [quotes, setQuotes] = useState<Map<string, OptionsScreenerRow>>(
    () => new Map(),
  );
  // Every tool opens in a dialog over the workspace, so the trader keeps the
  // instrument list behind it and never loses their place. The clicked row's
  // symbol is passed as instrumentHint / instrument so the stream scopes to
  // that name (E1) without PATCHing tenant desk config.
  const [dialog, setDialog] = useState<{ symbol: string; tool: string } | null>(
    null,
  );

  const initialOverlay = useMemo(() => overlayForTool(tool), [tool]);
  // Signal and Param Chart backends are AdminContext; refuse the deep link
  // instead of mounting a panel that 403s on every call it makes.
  const surfaceDenied =
    !isAdmin && (surface === "signal" || surface === "chart");
  const toolTitle =
    surface === "signal"
      ? "Signal Engine"
      : surface === "chart"
        ? "Param Chart"
        : // `ideas` / `backtest` are no longer offered at instrument level but
          // remain valid deep links, so keep titling them correctly.
          (LAB_WINDOW_TITLES[tool ?? "chain"] ?? "Options Lab");

  const loadDesk = useCallback(
    async (opts?: { refresh?: boolean }) => {
      if (opts?.refresh) setDeskRefreshing(true);
      try {
        const token = await getAccessToken();
        if (!token) return;
        // Snapshot=true reads the user's broker; the chat targets ride along,
        // so the rail and the books nav cost one request between them.
        const next = await getCustomerDesk(token, true);
        setDesk(next);
        setDeskError(null);
      } catch (reason) {
        setDeskError(
          reason instanceof Error ? reason.message : "Could not load your desk",
        );
      } finally {
        setDeskLoading(false);
        if (opts?.refresh) setDeskRefreshing(false);
      }
    },
    [getAccessToken],
  );

  useEffect(() => {
    if (!isLoaded || !isSignedIn) return;
    let cancelled = false;
    void (async () => {
      if (cancelled) return;
      await loadDesk();
    })();
    return () => {
      cancelled = true;
    };
  }, [isLoaded, isSignedIn, loadDesk]);

  // Which tools this viewer may open. Loaded on every route, unlike the picker
  // data below: the tool routes need it to refuse an admin-only deep link.
  useEffect(() => {
    if (!isLoaded || !isSignedIn) {
      setIsAdmin(false);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const token = await getAccessToken();
        if (!token || cancelled) return;
        const workspace = await getWorkspaceInfo(token);
        if (!cancelled) setIsAdmin(canOpenOrgAdmin(workspace));
      } catch {
        // Fail closed — an unknown viewer gets the end-user SKU, never the
        // operator tools.
        if (!cancelled) setIsAdmin(false);
      } finally {
        if (!cancelled) setAdminResolved(true);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoaded, isSignedIn, tenant.slug]);

  useEffect(() => {
    if (!isLoaded) return;
    if (!isSignedIn) {
      router.replace(
        `/sign-in?redirect_url=${encodeURIComponent(`/t/${tenant.slug}/workspace`)}`,
      );
      return;
    }
    if (instrument) return; // Picker data is only needed on the index route.
    let cancelled = false;
    let retryTimer = 0;

    const load = async (attempt: number) => {
      try {
        const token = await getAccessToken();
        if (!token || cancelled) return;
        // GET /config is ViewerContext — end users may read it.
        const res = await getOptionsLabConfig(token);
        if (cancelled) return;
        setPresets(res.presets ?? []);
        setDeskInstrument(res.config?.underlying_symbol || null);
        setError(null);
      } catch (reason) {
        if (cancelled) return;
        setError(
          reason instanceof Error ? reason.message : "Could not load instruments",
        );
        // Leave the list empty rather than stuck on "Loading instruments…" —
        // a failed load must render a state the trader can act on.
        setPresets((prev) => prev ?? []);
        // The backend allows 60 req/min; a burst (several desk windows, a dev
        // reload) can trip it. Back off and recover on our own.
        if (attempt < 4) {
          retryTimer = window.setTimeout(
            () => void load(attempt + 1),
            Math.min(30_000, 2_000 * 2 ** attempt),
          );
        }
      }
    };

    void load(0);
    return () => {
      cancelled = true;
      if (retryTimer) window.clearTimeout(retryTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoaded, isSignedIn, tenant.slug, instrument]);

  // Watchlist quotes for the landing rows. `fast` is spot-only — no chain scan —
  // and is server-cached, so polling here costs one batched quote call.
  useEffect(() => {
    if (instrument || !isLoaded || !isSignedIn) return;
    let cancelled = false;
    const controller = new AbortController();

    const load = async () => {
      if (document.visibilityState === "hidden") return;
      try {
        const token = await getAccessToken();
        if (!token || cancelled) return;
        const snap = await getOptionsScreener(
          token,
          "all",
          "fast",
          controller.signal,
        );
        if (cancelled || !snap.rows) return;
        setQuotes(
          new Map(snap.rows.map((row) => [row.underlying_symbol, row])),
        );
      } catch {
        // Quotes are decoration here — the list still works without them.
      }
    };

    void load();
    const timer = window.setInterval(() => void load(), WATCHLIST_POLL_MS);
    // Refresh straight away when the trader comes back to the tab.
    const onVisible = () => {
      if (document.visibilityState === "visible") void load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [getAccessToken, instrument, isLoaded, isSignedIn]);

  return (
    // Same provider as the original desk, so a chat reply can be pushed into
    // the Live trading composer.
    <DeskChatDraftProvider targets={desk?.chat_targets ?? []}>
    <div
      data-theme={dark ? "dark" : undefined}
      className="app-canvas flex h-dvh min-h-0 flex-col text-ink"
    >
      <header className="flex shrink-0 items-start justify-between gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-teal">
            {tenant.name}
          </p>
          <h1 className="mt-1 font-display text-3xl font-semibold tracking-tight">
            {instrument ? toolTitle : "Trading Desk"}
            {instrument ? (
              <span className="ml-2 align-middle text-lg font-medium text-slate-muted">
                {instrument}
              </span>
            ) : null}
          </h1>
        </div>
        <div className="flex shrink-0 items-center gap-2 pt-1">
          {instrument ? (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => router.push(`/t/${tenant.slug}/workspace`)}
            >
              Change instrument
            </Button>
          ) : null}
          <ChatAccountBar
            tenantSlug={tenant.slug}
            signInRedirect={`/t/${tenant.slug}/workspace`}
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

      <div className="flex min-h-0 flex-1">
        {/* Desk chat on the far left, as on the original desk. Admins and end
            users both get it — the teams a viewer may talk to already come
            scoped from the desk payload, so no gating is needed here. */}
        {isLoaded && isSignedIn && chatCollapsed ? (
          <button
            type="button"
            aria-label="Show desk chat"
            title="Show desk chat"
            onClick={() => setChatCollapsed(false)}
            className="flex h-full w-9 shrink-0 flex-col items-center justify-center gap-2 border-r border-line text-slate-muted transition hover:bg-raised/70 hover:text-ink"
          >
            <ChevronRightIcon className="h-3.5 w-3.5" />
            <span className="text-[10px] font-semibold uppercase tracking-[0.14em] [writing-mode:vertical-rl] rotate-180">
              Desk chat
            </span>
          </button>
        ) : null}

        {isLoaded && isSignedIn && !chatCollapsed ? (
          <aside className="flex h-full min-h-0 shrink-0 basis-[26%] flex-col overflow-hidden border-r border-line min-w-[20rem] max-w-[26rem]">
            {desk?.chat_targets.length ? (
              <WorkspaceDeskChat
                targets={desk.chat_targets}
                brokerTools={desk.broker_tools ?? []}
                allowPreview={isAdmin}
                onCollapse={() => setChatCollapsed(true)}
              />
            ) : (
              <div className="flex h-full items-center justify-center px-6 text-center text-sm text-slate-muted">
                {deskLoading
                  ? "Loading desk chat…"
                  : deskChatEmptyCopy(!isAdmin)}
              </div>
            )}
          </aside>
        ) : null}

        {/* Kite-style: the instrument list is a persistent left rail, so the
            trader never loses their place, and the chosen tool fills the
            centre. Dedicated /signal|/chart|/lab routes keep the full width. */}
        {!instrument && isLoaded && isSignedIn ? (
          <aside className="flex basis-[28%] shrink-0 flex-col overflow-hidden border-r border-line min-w-[22rem] max-w-[32rem]">
            <InstrumentPicker
              presets={presets}
              quotes={quotes}
              loadFailed={Boolean(error)}
              deskInstrument={deskInstrument}
              isAdmin={isAdmin}
              onOpenTool={(symbol, tool) => setDialog({ symbol, tool })}
            />
          </aside>
        ) : null}

      <div className="min-h-0 min-w-0 flex-1 basis-[70%] overflow-auto px-4 py-3">
        {!isLoaded || !isSignedIn ? (
          <p className="px-1 py-10 text-sm text-slate-muted">
            Redirecting to sign in…
          </p>
        ) : instrument ? (
          <div className="flex h-full min-h-0 flex-col">
            {!adminResolved ? (
              // Hold until the role is known. Mounting read-only and flipping
              // would tear down and re-open every stream the panel just opened.
              <p className="px-1 py-10 text-sm text-slate-muted">Loading…</p>
            ) : surfaceDenied ? (
              /* Signal and Param Chart are operator tools whose backends are
                 AdminContext. Say so, rather than mounting a panel that will
                 403 on every request it makes. */
              <div className="rounded-lg border border-line bg-canvas/40 px-6 py-10 text-center">
                <p className="text-sm text-ink">
                  {toolTitle} is an operator tool.
                </p>
                <p className="mt-1 text-sm text-slate-muted">
                  Your workspace role does not include it. Options Lab and
                  Automation are available from the instrument list.
                </p>
                <Button
                  className="mt-4"
                  variant="secondary"
                  size="sm"
                  onClick={() => router.push(`/t/${tenant.slug}/workspace`)}
                >
                  Back to instruments
                </Button>
              </div>
            ) : surface === "signal" ? (
              // The URL segment scopes the engine: the stream and the REST read
              // both carry ?instrument=, and a row is warmed for any watched name.
              <SignalMetricsPanel
                readOnly={!isAdmin}
                instrument={instrument}
              />
            ) : surface === "chart" ? (
              <ParamChartPanel active instrument={instrument} />
            ) : (
              /* Config PATCH stays admin, so end users get the chain read-only;
                 automation — Ideas / Backtest / paper Bots — is enabled for both. */
              <OptionsLabPanel
                active
                readOnly={!isAdmin}
                automationEnabled
                instrumentHint={instrument}
                initialOverlay={initialOverlay}
              />
            )}
          </div>
        ) : (
          /* No instrument chosen yet: the panel shows the trader's own broker
             account rather than an empty "pick something" page. */
          <DeskBooksNav
            className="h-full"
            data={desk}
            loading={deskLoading}
            refreshing={deskRefreshing}
            error={deskError}
            onRefresh={() => void loadDesk({ refresh: true })}
          />
        )}
        </div>
      </div>

      {dialog ? (
        <Modal
          title={LAB_WINDOW_TITLES[dialog.tool] ?? "Options Lab"}
          // Say exactly how each tool relates to the clicked row.
          subtitle={dialog.symbol}
          onClose={() => setDialog(null)}
          // Trading tools are intentional workspaces — do not dismiss on a
          // mis-click of the dimmed instrument rail. Close / Escape only.
          dismissOnBackdrop={false}
          actions={<TradingViewButton symbol={dialog.symbol} />}
        >
          <ToolSurface
            tool={dialog.tool}
            symbol={dialog.symbol}
            isAdmin={isAdmin}
          />
        </Modal>
      ) : null}

    </div>
    </DeskChatDraftProvider>
  );
}

/** Renders the tool chosen from the instrument menu (workspace dialog). */
function ToolSurface({
  tool,
  symbol,
  isAdmin,
}: {
  tool: string;
  symbol: string;
  isAdmin: boolean;
}) {
  // hideChartButton: the dialog header already shows TV beside the instrument.
  if (tool === "signal") {
    return (
      <SignalMetricsPanel
        readOnly={!isAdmin}
        instrument={symbol}
        hideChartButton
      />
    );
  }
  if (tool === "chart") {
    return <ParamChartPanel active instrument={symbol} hideChartButton />;
  }
  return (
    <OptionsLabPanel
      active
      readOnly={!isAdmin}
      automationEnabled
      instrumentHint={symbol}
      initialOverlay={overlayForTool(tool)}
      hideChartButton
    />
  );
}

/** Exchange tag from a Kite-style `EXCH:SYMBOL`. */
function exchangeOf(symbol: string): string {
  const [exchange] = symbol.split(":");
  return exchange && exchange !== symbol ? exchange : "";
}

function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/**
 * Market cap, in the units an Indian desk reads it in.
 *
 * NSE gives free-float market cap in ₹ crore, which runs to six digits for a
 * large cap — too wide for the rail — so anything past a lakh crore is shown
 * in lakh crore ("18.4L Cr").
 */
function formatMarketCap(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (value >= 100_000) return `${(value / 100_000).toFixed(2)}L Cr`;
  if (value >= 1_000) return `${Math.round(value).toLocaleString("en-IN")} Cr`;
  return `${value.toFixed(0)} Cr`;
}

/** P/E is meaningless for a loss-making name, and NSE sends nothing for one. */
function formatPeRatio(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toFixed(2);
}

type OpenInstrumentTool = (symbol: string, tool: string) => void;

function InstrumentRow({
  preset,
  quote,
  isDesk,
  isAdmin,
  onOpenTool,
}: {
  preset: SignalUnderlyingPreset;
  quote: OptionsScreenerRow | undefined;
  isDesk: boolean;
  isAdmin: boolean;
  onOpenTool: OpenInstrumentTool;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [anchor, setAnchor] = useState<{ top: number; right: number } | null>(
    null,
  );
  const rowRef = useRef<HTMLDivElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  // The rail clips (`overflow-hidden`) and scrolls, so an absolutely positioned
  // menu is cut off at the group boundary. Portal it to <body> and pin it to
  // the row's rect instead.
  const openMenu = () => {
    const rect = rowRef.current?.getBoundingClientRect();
    if (rect) {
      setAnchor({ top: rect.bottom, right: window.innerWidth - rect.right });
    }
    setMenuOpen(true);
  };

  // Dismiss on outside click / Escape — a menu that traps the pointer is worse
  // than no menu on a dense list. Scroll and resize invalidate the anchor, so
  // close rather than render it somewhere stale.
  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (rowRef.current?.contains(target) || menuRef.current?.contains(target)) {
        return;
      }
      setMenuOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    const onMove = () => setMenuOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", onMove);
    window.addEventListener("scroll", onMove, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onMove);
      window.removeEventListener("scroll", onMove, true);
    };
  }, [menuOpen]);

  const change = quote?.change ?? null;
  // A missing change is unknown, not flat — never colour it as a zero move.
  const up = change === null ? null : change >= 0;
  // Desk convention (see chainToneClass): teal = up, rose = down.
  const tone = up === null ? "text-slate-muted" : up ? "text-teal" : "text-rose";
  const exchange = exchangeOf(preset.symbol);
  const isIndex = preset.universe !== "equities";

  return (
    <div ref={rowRef} className="relative border-b border-line last:border-b-0">
      <button
        type="button"
        onClick={() => (menuOpen ? setMenuOpen(false) : openMenu())}
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        className={cn(
          "flex w-full items-center gap-2 px-3 py-2.5 text-left transition hover:bg-raised/60",
          menuOpen && "bg-raised/60",
        )}
      >
        <span className="flex min-w-0 flex-1 items-baseline gap-1.5">
          <span className={cn("truncate text-sm font-semibold", tone)}>
            {preset.label}
          </span>
          <span className="shrink-0 text-[10px] uppercase tracking-wide text-slate-muted">
            {isIndex ? "index" : exchange}
          </span>
          {isDesk ? (
            <span className="shrink-0 text-[10px] uppercase tracking-wide text-teal">
              desk
            </span>
          ) : null}
        </span>

        <span
          className="w-20 shrink-0 text-right text-xs tabular-nums text-slate-muted"
          title={
            quote?.market_cap == null
              ? "Market cap not loaded yet"
              : `Free-float market cap ₹${formatNumber(quote.market_cap, 0)} crore`
          }
        >
          {formatMarketCap(quote?.market_cap)}
        </span>
        <span
          className="w-12 shrink-0 text-right text-xs tabular-nums text-slate-muted"
          title={quote?.pe_ratio == null ? "P/E not available" : "Trailing P/E"}
        >
          {formatPeRatio(quote?.pe_ratio)}
        </span>

        {/* Absolute change is dropped in the rail — at this width, name, % and
            LTP are what a watchlist is scanned for. It stays in the tooltip. */}
        <span
          className={cn("w-16 shrink-0 text-right text-xs tabular-nums", tone)}
          title={change === null ? undefined : `Change ${formatNumber(change)}`}
        >
          {quote?.change_pct === null || quote?.change_pct === undefined
            ? "—"
            : `${formatNumber(quote.change_pct)}%`}
          {up === null ? "" : up ? " ▲" : " ▼"}
        </span>
        <span className={cn("w-20 shrink-0 text-right text-sm tabular-nums", tone)}>
          {formatNumber(quote?.spot ?? null)}
        </span>
        <span
          aria-hidden
          className="w-4 shrink-0 text-right text-base leading-none text-slate-muted"
        >
          ⋯
        </span>
      </button>

      {menuOpen && anchor
        ? createPortal(
        <div
          ref={menuRef}
          role="menu"
          style={{ top: anchor.top + 4, right: anchor.right }}
          className="fixed z-50 w-52 overflow-hidden rounded-lg border border-line bg-raised shadow-xl"
        >
          {toolsForViewer(isAdmin).map((entry) => {
            const isDeskTool = !entry.perInstrument;
            const Icon = entry.icon;
            return (
              <button
                key={entry.tool}
                type="button"
                role="menuitem"
                onClick={() => {
                  setMenuOpen(false);
                  onOpenTool(preset.symbol, entry.tool);
                }}
                className="group flex w-full items-center gap-2.5 border-b border-line px-3 py-2.5 text-left transition last:border-b-0 hover:bg-teal/10"
              >
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-teal/10 transition">
                  <Icon className={cn("h-3.5 w-3.5", entry.tone)} />
                </span>
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="text-sm font-medium transition group-hover:text-teal">
                    {entry.label}
                  </span>
                  <span className="truncate text-[10px] text-slate-muted">
                    {isDeskTool ? "desk instrument" : entry.hint}
                  </span>
                </span>
              </button>
            );
          })}
        </div>,
        document.body,
      )
        : null}
    </div>
  );
}

function InstrumentGroup({
  title,
  rows,
  quotes,
  deskInstrument,
  isAdmin,
  onOpenTool,
}: {
  title: string;
  rows: SignalUnderlyingPreset[];
  quotes: Map<string, OptionsScreenerRow>;
  deskInstrument: string | null;
  isAdmin: boolean;
  onOpenTool: OpenInstrumentTool;
}) {
  if (rows.length === 0) return null;
  return (
    <section className="mt-5">
      <h3 className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-muted">
        {title}
        <span className="ml-2 font-normal tracking-normal">{rows.length}</span>
      </h3>
      {/* No `overflow-hidden` here: it clipped each row's ⋯ dropdown at the
          group boundary. Rows round their own outer corners instead. */}
      <div className="mt-2 rounded-lg border border-line bg-canvas/60 [&>*:first-child]:rounded-t-lg [&>*:last-child]:rounded-b-lg">
        {rows.map((preset) => (
          <InstrumentRow
            key={preset.symbol}
            preset={preset}
            quote={quotes.get(preset.symbol)}
            isDesk={
              deskInstrument?.trim().toUpperCase() ===
              preset.symbol.trim().toUpperCase()
            }
            isAdmin={isAdmin}
            onOpenTool={onOpenTool}
          />
        ))}
      </div>
    </section>
  );
}

/**
 * Tools offered for a chosen instrument.
 *
 * `perInstrument` is the honest bit: Options Lab and its automation overlays
 * stream the URL instrument (E1), and Signal Engine is now scoped too — both
 * its SSE stream and its REST read carry `?instrument=`, and the engine warms
 * a matrix row for any watched name. Param Chart is the one that still runs on
 * the tenant desk instrument: picking a row there patches desk config, so it
 * moves the chart for everyone. Labelling it otherwise would repeat F1.
 */
type ToolEntry = {
  tool: string;
  label: string;
  hint: string;
  /** False = runs on the desk-wide instrument, not this row's. */
  perInstrument: boolean;
  icon: (props: { className?: string }) => React.ReactElement;
  /** Icon tint. Every offered tool is live, so all four carry the accent; the
      desk-wide vs instrument-scoped split is stated in the hint instead. */
  tone: string;
  /** Operator tools stay off the end-user Lab SKU. */
  adminOnly?: boolean;
};

/**
 * Four top-level tools, one per window.
 *
 * Ideas and Backtest are **not** peers here — they are stages of Automation
 * (screen an idea → backtest it → arm a bot) and live inside the Automation
 * window's own toolbar. Listing them at instrument level implied four separate
 * windows for what is one workflow.
 */
const LAB_TOOLS: ToolEntry[] = [
  {
    tool: "chain",
    label: "Options Lab",
    hint: "Chain & builder",
    perInstrument: true,
    icon: LayersIcon,
    tone: "text-teal",
  },
  {
    tool: "bots",
    label: "Automation",
    hint: "Ideas · Backtest · Bots",
    perInstrument: true,
    icon: PlayIcon,
    tone: "text-teal",
  },
];

const DESK_TOOLS: ToolEntry[] = [
  {
    tool: "signal",
    label: "Signal Engine",
    hint: "Checklist & live feeds",
    // Scoped per instrument: the stream and the REST read both carry
    // ?instrument=, and the engine warms a row for any watched name.
    perInstrument: true,
    icon: ChartLineIcon,
    tone: "text-teal",
    adminOnly: true,
  },
  {
    tool: "chart",
    label: "Param Chart",
    hint: "Monthly OHLC & params",
    perInstrument: false,
    icon: HistoryIcon,
    tone: "text-teal",
    adminOnly: true,
  },
];

const ALL_TOOLS: ToolEntry[] = [...LAB_TOOLS, ...DESK_TOOLS];

/**
 * End users get the Lab SKU (Options Lab + Automation); admins get all four.
 *
 * Signal Engine and Param Chart are operator tools whose backends are
 * AdminContext, so offering them to an end user would only produce a 403.
 */
export function toolsForViewer(isAdmin: boolean): ToolEntry[] {
  return isAdmin ? ALL_TOOLS : ALL_TOOLS.filter((entry) => !entry.adminOnly);
}

/** Window titles for every accepted `?tool=` value, menu item or deep link. */
const LAB_WINDOW_TITLES: Record<string, string> = {
  chain: "Options Lab",
  bots: "Automation",
  automation: "Automation",
  ideas: "Ideas",
  backtest: "Backtest",
  signal: "Signal Engine",
  chart: "Param Chart",
};

function InstrumentPicker({
  presets,
  quotes,
  loadFailed,
  deskInstrument,
  isAdmin,
  onOpenTool,
}: {
  presets: SignalUnderlyingPreset[] | null;
  quotes: Map<string, OptionsScreenerRow>;
  loadFailed: boolean;
  deskInstrument: string | null;
  isAdmin: boolean;
  onOpenTool: OpenInstrumentTool;
}) {
  const [query, setQuery] = useState("");

  const { indices, equities } = useMemo(() => {
    const term = query.trim().toUpperCase();
    const rows = (presets ?? []).filter(
      (p) =>
        !term ||
        p.label.toUpperCase().includes(term) ||
        p.symbol.toUpperCase().includes(term),
    );
    return {
      indices: rows.filter((p) => p.universe !== "equities"),
      equities: rows.filter((p) => p.universe === "equities"),
    };
  }, [presets, query]);

  if (presets === null) {
    return <p className="px-1 py-10 text-sm text-slate-muted">Loading instruments…</p>;
  }

  const total = indices.length + equities.length;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-line px-3 py-2.5">
        <input
        type="search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
          placeholder="Search NIFTY, SENSEX, RELIANCE…"
          aria-label="Search instruments"
          className="w-full rounded-lg border border-line bg-canvas/60 px-3 py-2 text-sm text-ink outline-none transition focus:border-teal"
        />
      </div>

      {/* The header must sit in the same horizontal box as a row, or the
          numbers do not line up under their labels. Rows are indented by the
          scroll container's px-2 and by their group's 1px border, so mirror
          both here (a transparent border reproduces the offset exactly) and
          reserve the ⋯ column's width. */}
      <div className="shrink-0 border-b border-line px-2">
        <div className="flex items-center gap-2 border border-transparent px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-muted">
          <span className="flex-1">Instrument</span>
          <span className="w-20 text-right">M.Cap</span>
          <span className="w-12 text-right">P/E</span>
          <span className="w-16 text-right">Chg %</span>
          <span className="w-20 text-right">LTP</span>
          <span aria-hidden className="w-4" />
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
      <InstrumentGroup
        title="Indices"
        rows={indices}
        quotes={quotes}
        deskInstrument={deskInstrument}
        isAdmin={isAdmin}
        onOpenTool={onOpenTool}
      />
      <InstrumentGroup
        title="Equities"
        rows={equities}
        quotes={quotes}
        deskInstrument={deskInstrument}
        isAdmin={isAdmin}
        onOpenTool={onOpenTool}
      />

      {total === 0 ? (
        <p className="mt-5 px-2 text-sm text-slate-muted">
          {query.trim()
            ? `No instrument matches “${query.trim()}”.`
            : loadFailed
              ? "Could not load instruments — retrying."
              : "No instruments are configured for this workspace yet."}
        </p>
      ) : null}

      </div>

    </div>
  );
}
