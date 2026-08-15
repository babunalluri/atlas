"use client";

import { Link } from "@/i18n/navigation";

import { DeskBooksPanel } from "@/components/domains/DeskBooksPanel";
import { deskChatEmptyCopy } from "@/components/domains/DeskChat";
import { WorkspaceDeskChat } from "@/components/domains/WorkspaceDeskChat";
import { TradingViewChartWidget } from "@/components/domains/TradingViewChartWidget";
import { MetricsDashboard } from "@/components/metrics/MetricsDashboard";
import { Button } from "@/components/ui/Button";
import type { DomainDashboard } from "@/lib/api/admin";

function formatFetchedAt(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString();
}

export function StockBrokerWorkspace({
  data,
  refreshing,
  onRefresh,
  variant = "admin",
}: {
  data: DomainDashboard;
  refreshing?: boolean;
  onRefresh: () => void;
  variant?: "admin" | "customer";
}) {
  const customer = variant === "customer";

  return (
    <div className="flex h-full min-h-0 flex-col lg:flex-row">
      <section className="flex min-h-[42vh] min-w-0 flex-1 flex-col border-b border-line lg:h-full lg:min-h-0 lg:min-w-[20rem] lg:max-w-[24rem] lg:flex-none lg:basis-[34%] lg:border-b-0 lg:border-r">
        {data.chat_targets.length > 0 ? (
          <WorkspaceDeskChat
            targets={data.chat_targets}
            brokerTools={data.broker_tools ?? []}
            allowPreview={!customer}
          />
        ) : (
          <div className="flex h-full items-center justify-center px-6 text-center text-sm text-slate-muted">
            {deskChatEmptyCopy(customer)}
          </div>
        )}
      </section>

      <section className="min-h-0 min-w-0 flex-1 overflow-y-auto px-5 py-5 lg:basis-[66%]">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-teal">
              {data.domain_label} workspace
            </p>
            <h1 className="mt-1 font-display text-3xl font-semibold tracking-tight">
              {customer ? "Your trading desk" : "Trading desk"}
            </h1>
            <p className="mt-1 max-w-xl text-sm text-slate-muted">
              {customer
                ? "Research is for analysis; live orders stay on Live trading. Chat tabs match the teams assigned to you. Use Refresh to load orders, positions, and watchlist from the toolkit on a trading team."
                : "Research is for analysis; live orders stay on Live trading. Chat tabs match assigned teams. Desk books load through the toolkit assigned on a trading team. Chart is TradingView. Refresh loads a new snapshot."}
            </p>
          </div>
          <div className="flex flex-col items-end gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={onRefresh}
              disabled={refreshing}
            >
              {refreshing ? "Refreshing…" : "Refresh"}
            </Button>
            <p className="text-[11px] text-slate-muted">
              Last fetched {formatFetchedAt(data.fetched_at)} · {data.range_days}d
            </p>
          </div>
        </header>

        <div className="mt-5">
          <TradingViewChartWidget />
        </div>

        <DeskBooksPanel
          snapshot={data.desk_snapshot}
          customer={customer}
          brokerTools={data.broker_tools ?? []}
        />

        {customer ? null : (
          <section className="mt-5 grid gap-3 sm:grid-cols-3">
            <div className="surface-panel rounded-xl p-4">
              <p className="th-label">Agents</p>
              <p className="mt-2 text-xl font-semibold tnum">
                {data.catalog.published_agents}/{data.catalog.agents}
              </p>
              <p className="mt-1 text-xs text-slate-muted">Published / total</p>
            </div>
            <div className="surface-panel rounded-xl p-4">
              <p className="th-label">Teams</p>
              <p className="mt-2 text-xl font-semibold tnum">
                {data.catalog.published_teams}/{data.catalog.teams}
              </p>
              <p className="mt-1 text-xs text-slate-muted">Published / total</p>
            </div>
            <div className="surface-panel rounded-xl p-4">
              <p className="th-label">Workflows</p>
              <p className="mt-2 text-xl font-semibold tnum">
                {data.catalog.published_workflows}/{data.catalog.workflows}
              </p>
              <p className="mt-1 text-xs text-slate-muted">Published / total</p>
            </div>
          </section>
        )}

        {!customer && data.quick_links.length > 0 ? (
          <section className="mt-3 flex flex-wrap gap-2">
            {data.quick_links.map((link) => (
              <Link
                key={link.href + link.label}
                href={link.href}
                className="rounded-full border border-line px-3 py-1.5 text-xs font-medium text-ink-soft transition hover:border-teal/40 hover:text-teal"
              >
                {link.label}
              </Link>
            ))}
          </section>
        ) : null}

        {customer ? null : (
          <div className="mt-6">
            <MetricsDashboard data={data.metrics} compact />
          </div>
        )}
      </section>
    </div>
  );
}
