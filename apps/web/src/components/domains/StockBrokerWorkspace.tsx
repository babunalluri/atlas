"use client";

import { useEffect, useState } from "react";

import { DeskBooksPanel } from "@/components/domains/DeskBooksPanel";
import { deskChatEmptyCopy } from "@/components/domains/DeskChat";
import { WorkspaceDeskChat } from "@/components/domains/WorkspaceDeskChat";
import { OptionsLabPanel } from "@/components/domains/OptionsLabPanel";
import { SignalMetricsPanel } from "@/components/domains/SignalMetricsPanel";
import { TradingViewChartWidget } from "@/components/domains/TradingViewChartWidget";
import { Button } from "@/components/ui/Button";
import { ChevronLeftIcon, ChevronRightIcon } from "@/components/ui/icons";
import type { DomainDashboard } from "@/lib/api/admin";
import { cn } from "@/lib/utils";

const CHAT_COLLAPSED_KEY = "atlas-desk-chat-collapsed";
const DESK_MAIN_TAB_KEY = "atlas-desk-main-tab";

type DeskMainTab = "signals" | "options-lab";

function useDeskMainTab() {
  const [tab, setTabState] = useState<DeskMainTab>("signals");

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(DESK_MAIN_TAB_KEY);
      if (stored === "options-lab" || stored === "signals") {
        setTabState(stored);
      } else if (stored === "automations") {
        // Former Automations desk tab → Options Lab (Bot overlay).
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
  const { collapsed: chatCollapsed, setCollapsed: setChatCollapsed } =
    useDeskChatCollapsed();
  const { tab: deskTab, setTab: setDeskTab } = useDeskMainTab();
  const hasChat = data.chat_targets.length > 0;

  return (
    <div className="flex h-full min-h-0 flex-col lg:flex-row">
      {hasChat && chatCollapsed ? (
        <button
          type="button"
          aria-label="Expand desk chat"
          title="Expand desk chat"
          onClick={() => setChatCollapsed(false)}
          className={cn(
            "flex shrink-0 items-center justify-center gap-2 border-b border-line bg-raised/60 px-3 py-2 text-xs font-medium text-slate-muted transition hover:bg-mist hover:text-ink lg:h-full lg:w-10 lg:flex-col lg:border-b-0 lg:border-r lg:px-0 lg:py-4",
          )}
        >
          <ChevronRightIcon />
          <span className="lg:[writing-mode:vertical-rl] lg:rotate-180 lg:text-[10px] lg:font-semibold lg:uppercase lg:tracking-[0.14em]">
            Desk chat
          </span>
        </button>
      ) : null}

      {hasChat && !chatCollapsed ? (
        <section className="relative flex min-h-[38vh] min-w-0 flex-1 flex-col border-b border-line lg:h-full lg:min-h-0 lg:min-w-[20rem] lg:max-w-[24rem] lg:flex-none lg:basis-[34%] lg:border-b-0 lg:border-r">
          <Button
            type="button"
            size="icon"
            variant="secondary"
            aria-label="Collapse desk chat"
            title="Collapse desk chat"
            icon={<ChevronLeftIcon />}
            onClick={() => setChatCollapsed(true)}
            className="absolute right-1.5 top-1.5 z-20 h-6 w-6 shadow-sm lg:top-1/2 lg:right-0 lg:h-7 lg:w-7 lg:translate-x-1/2 lg:-translate-y-1/2 lg:rounded-full lg:shadow-md"
          />
          <div className="flex min-h-0 flex-1 flex-col pt-8 lg:pt-0">
            <WorkspaceDeskChat
              targets={data.chat_targets}
              brokerTools={data.broker_tools ?? []}
              allowPreview={!customer}
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
          "min-h-0 min-w-0 flex-1 px-5 lg:basis-[66%]",
          showPageHeader ? "py-5" : "py-2",
          !customer
            ? "flex flex-col overflow-hidden"
            : "overflow-y-auto",
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

        {!customer ? (
          <div
            className={cn(
              showPageHeader && "mt-5",
              "flex min-h-0 flex-1 flex-col",
            )}
          >
            <div
              role="tablist"
              aria-label="Trading desk views"
              className="grid w-full shrink-0 grid-cols-2 gap-0.5 rounded-lg border border-line bg-canvas/70 p-0.5"
            >
              {(
                [
                  {
                    id: "signals" as const,
                    label: "Signal Engine",
                    hint: "Checklist & live feeds",
                  },
                  {
                    id: "options-lab" as const,
                    label: "Options Lab",
                    hint: "Chain, builder, bots",
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
            {deskTab === "signals" ? (
              <div className="mt-3 min-h-0 flex-1">
                <SignalMetricsPanel
                  deskSnapshot={data.desk_snapshot}
                  brokerTools={data.broker_tools ?? []}
                  refreshing={refreshing}
                  onRefreshBooks={onRefresh}
                  fetchedAt={data.fetched_at}
                  rangeDays={data.range_days}
                />
              </div>
            ) : (
              <div className="mt-3 min-h-0 flex-1">
                <OptionsLabPanel active />
              </div>
            )}
          </div>
        ) : null}

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
