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
}: {
  data: DomainDashboard;
  refreshing?: boolean;
  onRefresh?: () => void;
  variant?: "admin" | "customer";
  deskTitle?: string;
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

      <section className="min-h-0 min-w-0 flex-1 overflow-y-auto px-5 py-5 lg:basis-[66%]">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-teal">
              {data.domain_label} workspace
            </p>
            <h1 className="mt-1 font-display text-3xl font-semibold tracking-tight">
              {customer ? "Your trading desk" : deskTitle}
            </h1>
            <p className="mt-1 max-w-xl text-sm text-slate-muted">
              {customer
                ? "Research is for analysis; live orders stay on Live trading. Chat tabs match the teams assigned to you."
                : "Research is for analysis; live orders stay on Live trading. Chat tabs match assigned teams. Signal engine and Options Lab below."}
            </p>
          </div>
        </header>

        {customer ? (
          <div className="mt-5">
            <TradingViewChartWidget />
          </div>
        ) : null}

        {!customer ? (
          <div className="mt-5">
            <div
              role="tablist"
              aria-label="Trading desk views"
              className="grid w-full max-w-xl grid-cols-2 gap-1 rounded-xl border border-line bg-canvas/70 p-1.5"
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
                    hint: "Chain, builder & Greeks",
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
                    onClick={() => setDeskTab(id)}
                    className={cn(
                      "rounded-lg px-3 py-2.5 text-left transition duration-150",
                      selected
                        ? "bg-raised text-ink shadow-sm ring-1 ring-line/80"
                        : "text-slate-muted hover:bg-raised/50 hover:text-ink",
                    )}
                  >
                    <span className="font-display block text-base font-semibold tracking-tight">
                      {label}
                    </span>
                    <span
                      className={cn(
                        "mt-0.5 block text-[11px] leading-snug",
                        selected ? "text-slate-muted" : "text-slate-muted/80",
                      )}
                    >
                      {hint}
                    </span>
                  </button>
                );
              })}
            </div>
            {deskTab === "signals" ? (
              <SignalMetricsPanel />
            ) : (
              <OptionsLabPanel active />
            )}
          </div>
        ) : null}

        <DeskBooksPanel
          snapshot={data.desk_snapshot}
          customer={customer}
          brokerTools={data.broker_tools ?? []}
          refreshing={refreshing}
          onRefresh={onRefresh}
          fetchedAt={data.fetched_at}
          rangeDays={data.range_days}
        />
      </section>
    </div>
  );
}
