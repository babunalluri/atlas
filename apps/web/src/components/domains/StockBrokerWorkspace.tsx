"use client";

import { Link } from "@/i18n/navigation";

import { WorkspaceDeskChat } from "@/components/domains/WorkspaceDeskChat";
import { TradingViewChartWidget } from "@/components/domains/TradingViewChartWidget";
import { MetricsDashboard } from "@/components/metrics/MetricsDashboard";
import { Button } from "@/components/ui/Button";
import type { DomainDashboard, DomainDashboardWidget } from "@/lib/api/admin";

const GROUP_LABELS: Record<string, string> = {
  ops: "Desk activity",
  risk: "Live trading",
  signals: "Learning & paper",
  brokers: "Broker tools",
};

function formatFetchedAt(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString();
}

function WidgetCard({ widget }: { widget: DomainDashboardWidget }) {
  return (
    <div className="surface-panel rounded-xl p-4">
      <p className="th-label">{widget.label}</p>
      <p className="mt-2 text-xl font-semibold tnum">{widget.value}</p>
      <p className="mt-1 text-xs text-slate-muted">{widget.hint}</p>
    </div>
  );
}

export function StockBrokerWorkspace({
  data,
  refreshing,
  onRefresh,
}: {
  data: DomainDashboard;
  refreshing?: boolean;
  onRefresh: () => void;
}) {
  const groups = Object.keys(GROUP_LABELS).filter((group) =>
    data.widgets.some((widget) => (widget.group ?? "ops") === group),
  );

  return (
    <div className="flex h-full min-h-0 flex-col lg:flex-row">
      <section className="flex min-h-[42vh] min-w-0 flex-1 flex-col border-b border-line lg:h-full lg:min-h-0 lg:min-w-[20rem] lg:max-w-[24rem] lg:flex-none lg:basis-[34%] lg:border-b-0 lg:border-r">
        {data.chat_targets.length > 0 ? (
          <WorkspaceDeskChat
            targets={data.chat_targets}
            brokerTools={data.broker_tools ?? []}
          />
        ) : (
          <div className="flex h-full items-center justify-center px-6 text-center text-sm text-slate-muted">
            Provision the Stock Broker domain to chat with Learning, Paper trading, and Live trading.
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
              Trading desk
            </h1>
            <p className="mt-1 max-w-xl text-sm text-slate-muted">
              Three chats: Learning (concepts and ticker questions), Paper trading, Live
              trading. Broker widgets load through Live trading’s assigned toolkit. Chart
              is TradingView. Refresh loads a new snapshot.
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

        {groups.map((group) => (
          <section key={group} className="mt-5">
            <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-muted">
              {GROUP_LABELS[group]}
            </p>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {data.widgets
                .filter((widget) => (widget.group ?? "ops") === group)
                .map((widget) => (
                  <WidgetCard key={widget.id} widget={widget} />
                ))}
            </div>
          </section>
        ))}

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

        {data.broker_tools?.length ? (
          <section className="mt-4 flex flex-wrap gap-2">
            {data.broker_tools.map((tool) => (
              <Link
                key={tool.id}
                href="/admin/teams"
                className="rounded-full border border-line px-3 py-1.5 text-xs font-medium text-ink-soft transition hover:border-teal/40 hover:text-teal"
              >
                {tool.name}
                {tool.via_team_name ? ` · ${tool.via_team_name}` : ""}
                {!tool.published ? " · draft" : ""}
              </Link>
            ))}
          </section>
        ) : (
          <p className="mt-4 text-xs text-slate-muted">
            No broker toolkit on Live trading yet. Attach Groww, Kite, or any broker
            tool on that team (and on Learning for ticker quotes), publish, then refresh.
          </p>
        )}

        {data.quick_links.length > 0 ? (
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

        <div className="mt-6">
          <MetricsDashboard data={data.metrics} compact />
        </div>
      </section>
    </div>
  );
}
