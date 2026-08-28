"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";

import { DeskBooksPanel } from "@/components/domains/DeskBooksPanel";
import { deskChatEmptyCopy } from "@/components/domains/DeskChat";
import { WorkspaceDeskChat } from "@/components/domains/WorkspaceDeskChat";
import { ParamChartPanel } from "@/components/domains/ParamChartPanel";
import { SignalMetricsPanel } from "@/components/domains/SignalMetricsPanel";
import { TradingViewChartWidget } from "@/components/domains/TradingViewChartWidget";
import { ChevronRightIcon } from "@/components/ui/icons";
import type { DomainDashboard } from "@/lib/api/admin";
import { cn } from "@/lib/utils";

const CHAT_COLLAPSED_KEY = "atlas-desk-chat-collapsed";
const DESK_MAIN_TAB_KEY = "atlas-desk-main-tab";

const OptionsLabPanel = dynamic(
  () =>
    import("@/components/domains/OptionsLabPanel").then(
      (module) => module.OptionsLabPanel,
    ),
  {
    ssr: false,
    loading: () => (
      <div className="flex flex-1 items-center justify-center rounded-lg border border-line bg-canvas/40 px-6 py-12 text-sm text-slate-muted">
        Loading Options Lab…
      </div>
    ),
  },
);

type DeskMainTab = "signals" | "param-chart" | "options-lab";

function useDeskMainTab() {
  const [tab, setTabState] = useState<DeskMainTab>("signals");

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(DESK_MAIN_TAB_KEY);
      if (
        stored === "signals" ||
        stored === "param-chart" ||
        stored === "options-lab"
      ) {
        setTabState(stored);
      } else if (stored === "automations") {
        // Automations live inside Options Lab (overlay), not a top-level tab.
        setTabState("options-lab");
        window.localStorage.setItem(DESK_MAIN_TAB_KEY, "options-lab");
      }
    } catch {
      // private mode / blocked storage
    }
  }, []);

  function setTab(next: DeskMainTab) {
    setTabState(next);
    try {
      window.localStorage.setItem(DESK_MAIN_TAB_KEY, next);
    } catch {
      // private mode / blocked storage
    }
  }

  return { tab, setTab };
}

function useDeskChatCollapsed() {
  const [collapsed, setCollapsedState] = useState(false);

  useEffect(() => {
    try {
      setCollapsedState(window.localStorage.getItem(CHAT_COLLAPSED_KEY) === "1");
    } catch {
      // private mode / blocked storage
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

export function StockBrokerWorkspace({
  data,
  refreshing,
  onRefresh,
  variant = "admin",
  deskTitle = "Trading desk",
  showPageHeader = true,
}: {
  data: DomainDashboard;
  refreshing?: boolean;
  onRefresh?: () => void;
  variant?: "admin" | "customer";
  deskTitle?: string;
  /** When false, parent chrome already shows the desk title (avoids duplicate). */
  showPageHeader?: boolean;
}) {
  const customer = variant === "customer";
  const readOnly = customer;
  const { collapsed: chatCollapsed, setCollapsed: setChatCollapsed } =
    useDeskChatCollapsed();
  const { tab: deskTab, setTab: setDeskTab } = useDeskMainTab();
  const hasChat = data.chat_targets.length > 0;

  return (
    <div className="flex h-full min-h-0 flex-col lg:flex-row">
      {hasChat && chatCollapsed ? (
        <button
          type="button"
          aria-label="Show desk chat"
          title="Show desk chat"
          onClick={() => setChatCollapsed(false)}
          className="flex shrink-0 items-center justify-center gap-2 rounded-md border border-line bg-canvas/40 px-3 py-2 text-xs font-medium text-slate-muted transition hover:bg-raised/70 hover:text-ink lg:h-full lg:w-9 lg:flex-col lg:border-0 lg:border-r lg:px-0 lg:py-4"
        >
          <ChevronRightIcon className="h-3.5 w-3.5" />
          <span className="lg:[writing-mode:vertical-rl] lg:rotate-180 lg:text-[10px] lg:font-semibold lg:uppercase lg:tracking-[0.14em]">
            Desk chat
          </span>
        </button>
      ) : null}

      {hasChat && !chatCollapsed ? (
        <section className="relative flex min-h-[38vh] min-w-0 flex-1 flex-col border-b border-line lg:h-full lg:min-h-0 lg:min-w-[20rem] lg:max-w-[24rem] lg:flex-none lg:basis-[34%] lg:border-b-0 lg:border-r">
          <div className="flex min-h-0 flex-1 flex-col">
            <WorkspaceDeskChat
              targets={data.chat_targets}
              brokerTools={data.broker_tools ?? []}
              allowPreview={!customer}
              onCollapse={() => setChatCollapsed(true)}
            />
          </div>
        </section>
      ) : !hasChat ? (
        <section className="flex min-h-[38vh] min-w-0 flex-1 flex-col border-b border-line lg:h-full lg:min-h-0 lg:min-w-[20rem] lg:max-w-[24rem] lg:flex-none lg:basis-[34%] lg:border-b-0 lg:border-r">
          <div className="flex h-full items-center justify-center px-6 text-center text-sm text-slate-muted">
            {deskChatEmptyCopy(customer)}
          </div>
        </section>
      ) : null}

      <section
        className={cn(
          "min-h-0 min-w-0 flex-1 overlay-y-auto px-5 lg:basis-[66%]",
          showPageHeader ? "py-5" : "py-2",
          "flex flex-col",
        )}
      >
        {showPageHeader ? (
          <header className="flex shrink-0 flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-teal">
                {data.domain_label} workspace
              </p>
              <h1 className="mt-1 font-display text-3xl font-semibold tracking-tight">
                {customer ? "Your trading desk" : deskTitle}
              </h1>
            </div>
          </header>
        ) : null}

        {customer ? (
          <div className={showPageHeader ? "mt-5" : undefined}>
            <TradingViewChartWidget />
          </div>
        ) : null}

        <div
          className={cn(
            (showPageHeader || customer) && "mt-5",
            "flex min-h-0 flex-1 flex-col",
            customer && "min-h-[42rem]",
          )}
        >
          <div
            role="tablist"
            aria-label="Trading desk views"
            className="grid w-full shrink-0 grid-cols-1 gap-0.5 rounded-lg border border-line bg-canvas/70 p-0.5 sm:grid-cols-3"
          >
            {(
              [
                {
                  id: "signals" as const,
                  label: "Signal Engine",
                  hint: readOnly ? "Live checklist (view)" : "Checklist & live feeds",
                },
                {
                  id: "param-chart" as const,
                  label: "Param Chart",
                  hint: "Monthly OHLC & shared params",
                },
                {
                  id: "options-lab" as const,
                  label: "Options Lab",
                  hint: readOnly
                    ? "Chain & strategies (view)"
                    : "Chain, builder & books",
                },
              ] as const
            ).map(({ id, label, hint }) => {
              const selected = deskTab === id;
              return (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  title={hint}
                  onClick={() => setDeskTab(id)}
                  className={cn(
                    "flex items-baseline gap-2 rounded-md px-3 py-2 text-left transition duration-150",
                    selected
                      ? "bg-raised text-ink shadow-sm ring-1 ring-line/80"
                      : "text-slate-muted hover:bg-raised/50 hover:text-ink",
                  )}
                >
                  <span className="text-base font-semibold tracking-tight">
                    {label}
                  </span>
                  <span className="truncate text-sm text-slate-muted">
                    {hint}
                  </span>
                </button>
              );
            })}
          </div>
          {/* Keep Signal and Param Chart mounted (hidden) so SSE + Redis
              watch stay alive when flipping tabs. Unmounting aborted the
              stream and left the board on dashes / a hist reload. */}
          <div
            className={cn(
              "mt-3 flex min-h-0 flex-1 flex-col overflow-hidden",
              deskTab !== "signals" && "hidden",
            )}
            aria-hidden={deskTab !== "signals"}
          >
            <SignalMetricsPanel
              deskSnapshot={data.desk_snapshot}
              brokerTools={data.broker_tools ?? []}
              refreshing={refreshing}
              onRefreshBooks={onRefresh}
              fetchedAt={data.fetched_at}
              rangeDays={data.range_days}
              readOnly={readOnly}
            />
          </div>
          <div
            className={cn(
              "mt-3 flex min-h-0 flex-1 flex-col overflow-hidden",
              deskTab !== "param-chart" && "hidden",
            )}
            aria-hidden={deskTab !== "param-chart"}
          >
            <ParamChartPanel active />
          </div>
          {/* Mount Options Lab only on its tab (unmount stops SSE / sandbox).
              Do not keep hidden with active=true — unlike Signal / Param Chart. */}
          {deskTab === "options-lab" ? (
            <div className="mt-3 flex min-h-0 flex-1 flex-col overflow-hidden">
              <OptionsLabPanel active readOnly={readOnly} />
            </div>
          ) : null}
        </div>

        {customer ? (
          <DeskBooksPanel
            snapshot={data.desk_snapshot}
            customer={customer}
            brokerTools={data.broker_tools ?? []}
            refreshing={refreshing}
            onRefresh={onRefresh}
            fetchedAt={data.fetched_at}
            rangeDays={data.range_days}
          />
        ) : null}
      </section>
    </div>
  );
}
